from flask import Flask, render_template, request, redirect, url_for, send_file, session, flash
import os
import sqlite3
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
from io import BytesIO
from fpdf import FPDF
import smtplib
from email.message import EmailMessage

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.secret_key = 'hemmelig-nøgle'
app.permanent_session_lifetime = timedelta(minutes=60)
DATABASE = os.path.join(os.path.dirname(__file__), 'reports.db')

def init_db():
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, is_admin INTEGER DEFAULT 0)")
    cur.execute("CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY, timestamp TEXT, location TEXT, subject TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS entries (id INTEGER PRIMARY KEY, report_id INTEGER, time TEXT, description TEXT, image TEXT, FOREIGN KEY(report_id) REFERENCES reports(id))")
    cur.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO users (username, password, is_admin) VALUES ('admin', 'admin123', 1)")
        for i in range(1, 7):
            cur.execute("INSERT INTO users (username, password) VALUES (?, ?)", (f'user{i}', 'test123'))
    conn.commit()
    conn.close()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = sqlite3.connect(DATABASE)
        cur = conn.cursor()
        cur.execute("SELECT id, is_admin FROM users WHERE username=? AND password=?", (username, password))
        user = cur.fetchone()
        conn.close()
        if user:
            session.permanent = True
            session['logged_in'] = True
            session['user_id'] = user[0]
            session['is_admin'] = bool(user[1])
            return redirect(url_for('index'))
        else:
            error = "Forkert brugernavn eller adgangskode."
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if not session.get('logged_in') or not session.get('is_admin'):
        return redirect(url_for('login'))
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    if request.method == 'POST':
        if 'add_user' in request.form:
            username = request.form['new_username']
            password = request.form['new_password']
            cur.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, password))
        elif 'delete_user' in request.form:
            user_id = request.form['delete_user']
            cur.execute("DELETE FROM users WHERE id=? AND is_admin=0", (user_id,))
        elif 'change_password_user' in request.form:
            user_id = request.form['change_password_user']
            new_password = request.form['new_password']
            cur.execute("UPDATE users SET password=? WHERE id=?", (new_password, user_id))
    cur.execute("SELECT id, username, is_admin FROM users")
    users = cur.fetchall()
    conn.commit()
    conn.close()
    return render_template('admin.html', users=users)

@app.route('/', methods=['GET', 'POST'])
def index():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    if request.method == 'POST':
        location = request.form['location']
        subject = request.form['subject']
        timestamp = datetime.now().isoformat()
        conn = sqlite3.connect(DATABASE)
        cur = conn.cursor()
        cur.execute("INSERT INTO reports(timestamp, location, subject) VALUES (?, ?, ?)", (timestamp, location, subject))
        report_id = cur.lastrowid
        times = request.form.getlist('entry_time')
        descs = request.form.getlist('entry_desc')
        images = request.files.getlist('entry_image')
        for t, d, f in zip(times, descs, images):
            filename = ''
            if f and allowed_file(f.filename):
                filename = secure_filename(f.filename)
                f.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            cur.execute("INSERT INTO entries(report_id, time, description, image) VALUES (?, ?, ?, ?)", (report_id, t, d, filename))
        conn.commit()
        conn.close()
        return redirect(url_for('tak'))
    init_db()
    return render_template('index.html')

@app.route('/tak')
def tak():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('tak.html')

@app.route('/rapporter')
def rapporter():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute("SELECT id, timestamp, location, subject FROM reports ORDER BY timestamp DESC")
    reports = cur.fetchall()
    conn.close()
    return render_template('rapporter.html', reports=reports)

@app.route('/rapport/<int:report_id>', methods=['GET', 'POST'])
def vis_rapport(report_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute("SELECT timestamp, location, subject FROM reports WHERE id=?", (report_id,))
    report = cur.fetchone()
    cur.execute("SELECT time, description, image FROM entries WHERE report_id=?", (report_id,))
    entries = cur.fetchall()
    conn.close()
    if request.method == 'POST':
        modtager = request.form['email']
        pdf = generer_pdf(report, entries)
        if send_email_pdf(modtager, pdf):
            flash("PDF sendt til " + modtager, "success")
        else:
            flash("Der opstod en fejl ved afsendelse.", "danger")
    return render_template('rapport.html', report=report, entries=entries, report_id=report_id)

@app.route('/rapport/<int:report_id>/pdf')
def download_pdf(report_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute("SELECT timestamp, location, subject FROM reports WHERE id=?", (report_id,))
    report = cur.fetchone()
    cur.execute("SELECT time, description FROM entries WHERE report_id=?", (report_id,))
    entries = cur.fetchall()
    conn.close()
    pdf = generer_pdf(report, entries)
    return send_file(pdf, as_attachment=True, download_name='rapport.pdf', mimetype='application/pdf')

def generer_pdf(report, entries):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, txt="Bjærgningsrapport", ln=True, align='C')
    pdf.ln(10)
    pdf.cell(200, 10, txt=f"Sted: {report[1]}", ln=True)
    pdf.cell(200, 10, txt=f"Opgave: {report[2]}", ln=True)
    pdf.cell(200, 10, txt=f"Dato: {report[0][:16].replace('T', ' ')}", ln=True)
    pdf.ln(10)
    pdf.set_font("Arial", style='B', size=12)
    pdf.cell(200, 10, txt="Hændelsesforløb:", ln=True)
    pdf.set_font("Arial", size=11)
    for time, desc in entries:
        pdf.multi_cell(0, 8, txt=f"{time} - {desc}", align='L')
        pdf.ln(1)
    pdf_bytes = pdf.output(dest='S').encode('latin1')
    buffer = BytesIO(pdf_bytes)
    buffer.seek(0)
    return buffer

def send_email_pdf(modtager, pdf_data):
    try:
        sender = os.environ.get('EMAIL_SENDER')
        password = os.environ.get('EMAIL_PASSWORD')
        if not sender or not password:
            return False
        msg = EmailMessage()
        msg['Subject'] = "Bjærgningsrapport PDF"
        msg['From'] = sender
        msg['To'] = modtager
        msg.set_content("Vedhæftet finder du PDF-rapporten.")
        msg.add_attachment(pdf_data.read(), maintype='application', subtype='pdf', filename='rapport.pdf')
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(sender, password)
            smtp.send_message(msg)
        return True
    except Exception as e:
        print("Fejl ved e-mail:", e)
        return False

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
