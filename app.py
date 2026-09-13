import os
import csv
import io
import sqlite3
from datetime import date, datetime
from functools import wraps
from urllib.parse import quote
import re
from uuid import uuid4


from flask import Flask, Response, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE_DIR, "clinic.db")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-in-production")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

# IMPORTANT: Replace this with the clinic's real Google Pay / UPI ID before production.
GPAY_UPI_ID = os.environ.get("GPAY_UPI_ID", "7867873401@axl")

CLINIC = {
    "name": "Rani Muthu Clinic",
    "doctor": "Dr. Ravikumar Muthukaluvan",
    "short_doctor": "M Ravi Kumar",
    "qualifications": "MBBS, D-Ortho, MRCS (Edinburgh), MRCGP (London)",
    "bma_membership": "BMA Member",
    "experience": "27+ years of clinical experience",
    "phone": "+91 73585 25232",
    "email": "ranimuthuclinic@gmail.com",
    "hours": "Mon–Fri 8:00 AM–5:00 PM; Sat 9:00 AM–1:00 PM; Closed Sundays",
    "address": "M7P2+P2G, Gudalur, Tamil Nadu 625518 — Dindigul - Theni - Kottarakkara Highway",
}

SERVICES = [
    ("General Consultation", 100, "Primary care assessment and treatment planning."),
    ("Blood Pressure Check", 50, "Routine blood pressure monitoring."),
    ("Joint Injection", 100, "Joint injection service subject to clinical assessment."),
    ("Follow-up Consultation", 100, "Review of an existing consultation or treatment."),
    ("Blood Sugar Check", 50, "Basic blood sugar screening."),
]


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            phone TEXT NOT NULL,
            parent_phone TEXT,
            password TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS doctors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            doctor_id TEXT UNIQUE,
            password TEXT NOT NULL,
            specialty TEXT NOT NULL,
            experience TEXT,
            bio TEXT
        );

        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            fee INTEGER NOT NULL,
            description TEXT
        );

        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            doctor_id INTEGER NOT NULL,
            service_id INTEGER NOT NULL,
            appointment_date TEXT NOT NULL,
            appointment_time TEXT NOT NULL,
            reason TEXT,
            status TEXT DEFAULT 'Pending',
            payment_status TEXT DEFAULT 'Unpaid',
            transaction_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(patient_id) REFERENCES patients(id) ON DELETE CASCADE,
            FOREIGN KEY(doctor_id) REFERENCES doctors(id),
            FOREIGN KEY(service_id) REFERENCES services(id)
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER UNIQUE NOT NULL,
            patient_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            method TEXT NOT NULL DEFAULT 'Google Pay / UPI',
            transaction_id TEXT NOT NULL,
            paid_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(appointment_id) REFERENCES appointments(id) ON DELETE CASCADE,
            FOREIGN KEY(patient_id) REFERENCES patients(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_number TEXT UNIQUE NOT NULL,
            payment_id INTEGER UNIQUE NOT NULL,
            appointment_id INTEGER UNIQUE NOT NULL,
            patient_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(payment_id) REFERENCES payments(id) ON DELETE CASCADE,
            FOREIGN KEY(appointment_id) REFERENCES appointments(id) ON DELETE CASCADE,
            FOREIGN KEY(patient_id) REFERENCES patients(id) ON DELETE CASCADE
        );
        """
    )

    # Backward-compatible migration for existing clinic.db files.
    doctor_columns = {row[1] for row in conn.execute("PRAGMA table_info(doctors)").fetchall()}
    if "doctor_id" not in doctor_columns:
        conn.execute("ALTER TABLE doctors ADD COLUMN doctor_id TEXT")
    patient_columns = {row[1] for row in conn.execute("PRAGMA table_info(patients)").fetchall()}
    if "parent_phone" not in patient_columns:
        conn.execute("ALTER TABLE patients ADD COLUMN parent_phone TEXT")

    # Keep the single clinic doctor account aligned with the public clinic email.
    existing_old = conn.execute(
        "SELECT id FROM doctors WHERE email=?", ("doctor@ranimuthuclinic.com",)
    ).fetchone()
    if existing_old:
        conn.execute(
            "UPDATE doctors SET email=?, doctor_id=? WHERE id=?", (CLINIC["email"], "ravortho", existing_old["id"])
        )

    doctor = conn.execute(
        "SELECT id FROM doctors WHERE email=?", (CLINIC["email"],)
    ).fetchone()
    if doctor:
        conn.execute("UPDATE doctors SET doctor_id=?, password=? WHERE id=?", ("ravortho", generate_password_hash("Myloravi@2509"), doctor["id"]))
    if not doctor:
        conn.execute(
            """
            INSERT INTO doctors(name,email,doctor_id,password,specialty,experience,bio)
            VALUES(?,?,?,?,?,?,?)
            """,
            (
                CLINIC["doctor"],
                CLINIC["email"],
                "ravortho",
                generate_password_hash("Myloravi@2509"),
                "General Practice • Sports Medicine • Skin Conditions",
                CLINIC["experience"],
                (
                    "UK-registered General Practitioner (GP) Partner practicing at "
                    "Eastbrook Surgery / Rush Green Medical Centre in Romford, London. "
                    "He has over 27 years of clinical experience and completed his MBBS "
                    "from The Tamil Nadu Dr. M.G.R. Medical University."
                ),
            ),
        )

    for name, fee, description in SERVICES:
        conn.execute(
            "INSERT OR IGNORE INTO services(name,fee,description) VALUES(?,?,?)",
            (name, fee, description),
        )

    conn.commit()
    conn.close()


@app.context_processor
def inject_clinic():
    return {
        "clinic": CLINIC,
        "services": SERVICES,
        "now_date": date.today().isoformat(),
        "gpay_upi_id": GPAY_UPI_ID,
    }


def patient_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        patient_id = session.get("patient_id")
        if not patient_id:
            flash("Please login as a patient first.", "warning")
            return redirect(url_for("patient_login"))
        conn = get_db()
        exists = conn.execute("SELECT id FROM patients WHERE id=?", (patient_id,)).fetchone()
        conn.close()
        if not exists:
            session.clear()
            flash("Your session is no longer valid. Please login again.", "warning")
            return redirect(url_for("patient_login"))
        return view(*args, **kwargs)

    return wrapped


def doctor_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "doctor_id" not in session:
            flash("Please login as a doctor first.", "warning")
            return redirect(url_for("doctor_login"))
        return view(*args, **kwargs)

    return wrapped


def make_gpay_link(amount, appointment_id, service_name):
    if not GPAY_UPI_ID:
        return ""
    params = (
        f"pa={quote(GPAY_UPI_ID)}&pn={quote(CLINIC['name'])}"
        f"&am={amount:.2f}&cu=INR&tn={quote('Clinic appointment ' + str(appointment_id) + ' - ' + service_name)}"
    )
    return "upi://pay?" + params


def get_or_create_bill(conn, appointment_row, transaction_id):
    existing = conn.execute(
        """
        SELECT b.*, p.transaction_id, p.method, p.paid_at
        FROM bills b JOIN payments p ON p.id=b.payment_id
        WHERE b.appointment_id=?
        """,
        (appointment_row["id"],),
    ).fetchone()
    if existing:
        return existing

    payment = conn.execute(
        "SELECT * FROM payments WHERE appointment_id=?", (appointment_row["id"],)
    ).fetchone()
    if not payment:
        cur = conn.execute(
            """
            INSERT INTO payments(appointment_id,patient_id,amount,method,transaction_id)
            VALUES(?,?,?,?,?)
            """,
            (
                appointment_row["id"],
                appointment_row["patient_id"],
                appointment_row["fee"],
                "Google Pay / UPI",
                transaction_id,
            ),
        )
        payment_id = cur.lastrowid
    else:
        payment_id = payment["id"]

    bill_number = "RM-" + datetime.now().strftime("%Y%m%d") + "-" + uuid4().hex[:6].upper()
    cur = conn.execute(
        """
        INSERT INTO bills(bill_number,payment_id,appointment_id,patient_id,amount)
        VALUES(?,?,?,?,?)
        """,
        (
            bill_number,
            payment_id,
            appointment_row["id"],
            appointment_row["patient_id"],
            appointment_row["fee"],
        ),
    )
    bill_id = cur.lastrowid
    return conn.execute(
        """
        SELECT b.*, p.transaction_id, p.method, p.paid_at
        FROM bills b JOIN payments p ON p.id=b.payment_id
        WHERE b.id=?
        """,
        (bill_id,),
    ).fetchone()


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/about")
def about():
    conn = get_db()
    doctor = conn.execute("SELECT * FROM doctors ORDER BY id LIMIT 1").fetchone()
    conn.close()
    return render_template("about.html", doctor=doctor)


@app.route("/fees")
def fees():
    conn = get_db()
    fee_rows = conn.execute("SELECT * FROM services ORDER BY id").fetchall()
    conn.close()
    return render_template("fees.html", fee_rows=fee_rows)


@app.route("/contact")
def contact():
    return render_template("contact.html")


@app.route("/patient/register", methods=["GET", "POST"])
def patient_register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        parent_phone = request.form.get("parent_phone", "").strip()
        password = request.form.get("password", "")

        if not all([name, email, phone, password]):
            flash("Please complete all fields.", "danger")
            return render_template("patient_register.html")
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return render_template("patient_register.html")

        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO patients(name,email,phone,parent_phone,password) VALUES(?,?,?,?,?)",
                (name, email, phone, parent_phone, generate_password_hash(password)),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            flash("That email is already registered.", "danger")
            conn.close()
            return render_template("patient_register.html")
        conn.close()
        flash("Account created successfully. Please login.", "success")
        return redirect(url_for("patient_login"))

    return render_template("patient_register.html")


@app.route("/patient/login", methods=["GET", "POST"])
def patient_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        conn = get_db()
        patient = conn.execute("SELECT * FROM patients WHERE email=?", (email,)).fetchone()
        conn.close()

        if patient and check_password_hash(patient["password"], password):
            session.clear()
            session["patient_id"] = patient["id"]
            session["patient_name"] = patient["name"]
            return redirect(url_for("patient_dashboard"))
        flash("Invalid email or password.", "danger")

    return render_template("patient_login.html")


@app.route("/patient/dashboard")
@patient_required
def patient_dashboard():
    conn = get_db()
    appointments = conn.execute(
        """
        SELECT a.*, d.name AS doctor_name, s.name AS service_name, s.fee,
               b.id AS bill_id, b.bill_number
        FROM appointments a
        JOIN doctors d ON d.id=a.doctor_id
        JOIN services s ON s.id=a.service_id
        LEFT JOIN bills b ON b.appointment_id=a.id
        WHERE a.patient_id=?
        ORDER BY a.appointment_date DESC, a.appointment_time DESC
        """,
        (session["patient_id"],),
    ).fetchall()
    next_appointment = conn.execute(
        """
        SELECT a.*, d.name AS doctor_name, s.name AS service_name, s.fee
        FROM appointments a
        JOIN doctors d ON d.id=a.doctor_id
        JOIN services s ON s.id=a.service_id
        WHERE a.patient_id=?
          AND a.status IN ('Pending','Confirmed')
          AND a.appointment_date >= ?
        ORDER BY a.appointment_date ASC, a.appointment_time ASC
        LIMIT 1
        """,
        (session["patient_id"], date.today().isoformat()),
    ).fetchone()
    bills = conn.execute(
        """
        SELECT b.*, p.transaction_id, p.method, p.paid_at, a.appointment_date,
               a.appointment_time, s.name AS service_name
        FROM bills b
        JOIN payments p ON p.id=b.payment_id
        JOIN appointments a ON a.id=b.appointment_id
        JOIN services s ON s.id=a.service_id
        WHERE b.patient_id=? ORDER BY b.id DESC
        """,
        (session["patient_id"],),
    ).fetchall()
    conn.close()

    paid = sum(row["fee"] for row in appointments if row["payment_status"] == "Paid")
    upcoming = sum(row["status"] in ("Pending", "Confirmed") for row in appointments)
    completed = sum(row["status"] == "Completed" for row in appointments)
    return render_template(
        "patient_dashboard.html",
        appointments=appointments,
        bills=bills,
        total=len(appointments),
        upcoming=upcoming,
        completed=completed,
        paid=paid,
        next_appointment=next_appointment,
    )


@app.route("/appointment", methods=["GET", "POST"])
@patient_required
def appointment():
    conn = get_db()
    doctor = conn.execute("SELECT * FROM doctors ORDER BY id LIMIT 1").fetchone()
    fee_rows = conn.execute("SELECT * FROM services ORDER BY id").fetchall()

    if request.method == "POST":
        appointment_date = request.form.get("appointment_date", "")
        appointment_time = request.form.get("appointment_time", "")
        service_id = request.form.get("service_id", "")
        reason = request.form.get("reason", "").strip()

        try:
            if date.fromisoformat(appointment_date) < date.today():
                raise ValueError("Please choose today or a future date.")
            if not appointment_time:
                raise ValueError("Please choose an appointment time.")
            service = conn.execute("SELECT * FROM services WHERE id=?", (service_id,)).fetchone()
            if not service:
                raise ValueError("Please choose a valid service.")
        except ValueError as exc:
            conn.close()
            flash(str(exc), "danger")
            return render_template("appointment.html", doctor=doctor, fee_rows=fee_rows)

        cursor = conn.execute(
            """
            INSERT INTO appointments(
                patient_id,doctor_id,service_id,appointment_date,appointment_time,reason
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                session["patient_id"],
                doctor["id"],
                service_id,
                appointment_date,
                appointment_time,
                reason,
            ),
        )
        conn.commit()
        appointment_id = cursor.lastrowid
        conn.close()
        return redirect(url_for("payment", appointment_id=appointment_id))

    conn.close()
    return render_template("appointment.html", doctor=doctor, fee_rows=fee_rows)


@app.route("/payment/<int:appointment_id>", methods=["GET", "POST"])
@patient_required
def payment(appointment_id):
    conn = get_db()
    appointment_row = conn.execute(
        """
        SELECT a.*, d.name AS doctor_name, s.name AS service_name, s.fee
        FROM appointments a
        JOIN doctors d ON d.id=a.doctor_id
        JOIN services s ON s.id=a.service_id
        WHERE a.id=? AND a.patient_id=?
        """,
        (appointment_id, session["patient_id"]),
    ).fetchone()

    if not appointment_row:
        conn.close()
        flash("Appointment not found.", "danger")
        return redirect(url_for("patient_dashboard"))

    if appointment_row["payment_status"] == "Paid":
        bill = conn.execute(
            """
            SELECT b.*, p.transaction_id, p.method, p.paid_at
            FROM bills b JOIN payments p ON p.id=b.payment_id
            WHERE b.appointment_id=?
            """,
            (appointment_id,),
        ).fetchone()
        conn.close()
        if bill:
            return redirect(url_for("bill", bill_id=bill["id"]))

    if request.method == "POST":
        transaction_id = request.form.get("transaction_id", "").strip()
        if not transaction_id:
            flash("Please enter the UPI transaction/reference ID.", "danger")
        else:
            conn.execute(
                """
                UPDATE appointments
                SET payment_status='Paid', status='Confirmed', transaction_id=?
                WHERE id=? AND patient_id=?
                """,
                (transaction_id, appointment_id, session["patient_id"]),
            )
            bill = get_or_create_bill(conn, appointment_row, transaction_id)
            conn.commit()
            conn.close()
            flash("Payment saved. Your bill/receipt is ready.", "success")
            return redirect(url_for("bill", bill_id=bill["id"]))

    gpay_link = make_gpay_link(appointment_row["fee"], appointment_id, appointment_row["service_name"])
    conn.close()
    return render_template("payment.html", appointment=appointment_row, gpay_link=gpay_link, gpay_upi_id=GPAY_UPI_ID)


@app.route("/bill/<int:bill_id>")
@patient_required
def bill(bill_id):
    conn = get_db()
    row = conn.execute(
        """
        SELECT b.*, p.transaction_id, p.method, p.paid_at,
               a.appointment_date, a.appointment_time, a.reason,
               d.name AS doctor_name, s.name AS service_name,
               pt.name AS patient_name, pt.email AS patient_email, pt.phone AS patient_phone,
               pt.parent_phone AS parent_phone
        FROM bills b
        JOIN payments p ON p.id=b.payment_id
        JOIN appointments a ON a.id=b.appointment_id
        JOIN doctors d ON d.id=a.doctor_id
        JOIN services s ON s.id=a.service_id
        JOIN patients pt ON pt.id=b.patient_id
        WHERE b.id=? AND b.patient_id=?
        """,
        (bill_id, session["patient_id"]),
    ).fetchone()
    conn.close()
    if not row:
        flash("Bill not found.", "danger")
        return redirect(url_for("patient_dashboard"))
    bill_url = request.host_url.rstrip("/") + url_for("bill", bill_id=bill_id)
    whatsapp_text = (
        f"Rani Muthu Clinic bill {row['bill_number']}\n"
        f"Patient: {row['patient_name']}\n"
        f"Service: {row['service_name']}\n"
        f"Amount paid: ₹{row['amount']}\n"
        f"Transaction ID: {row['transaction_id']}\n"
        f"Bill: {bill_url}"
    )
    parent_phone = re.sub(r"\D", "", row["parent_phone"] or "")
    if len(parent_phone) == 10:
        parent_phone = "91" + parent_phone
    whatsapp_url = "https://wa.me/" + parent_phone + "?text=" + quote(whatsapp_text) if parent_phone else "https://wa.me/?text=" + quote(whatsapp_text)
    return render_template("bill.html", bill=row, whatsapp_url=whatsapp_url)


@app.route("/doctor/login", methods=["GET", "POST"])
def doctor_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        conn = get_db()
        doctor = conn.execute("SELECT * FROM doctors WHERE lower(email)=? OR lower(doctor_id)=?", (email, email)).fetchone()
        conn.close()
        if doctor and check_password_hash(doctor["password"], password):
            session.clear()
            session["doctor_id"] = doctor["id"]
            session["doctor_name"] = doctor["name"]
            return redirect(url_for("doctor_dashboard"))
        flash("Invalid doctor login.", "danger")
    return render_template("doctor_login.html")


@app.route("/doctor/dashboard")
@doctor_required
def doctor_dashboard():
    conn = get_db()
    appointments = conn.execute(
        """
        SELECT a.*, p.name AS patient_name, p.phone, p.email,
               s.name AS service_name, s.fee, b.bill_number
        FROM appointments a
        JOIN patients p ON p.id=a.patient_id
        JOIN services s ON s.id=a.service_id
        LEFT JOIN bills b ON b.appointment_id=a.id
        WHERE a.doctor_id=?
        ORDER BY a.appointment_date DESC, a.appointment_time DESC
        """,
        (session["doctor_id"],),
    ).fetchall()
    payments = conn.execute(
        """
        SELECT p.*, b.bill_number, pt.name AS patient_name, a.appointment_date,
               s.name AS service_name
        FROM payments p
        JOIN bills b ON b.payment_id=p.id
        JOIN patients pt ON pt.id=p.patient_id
        JOIN appointments a ON a.id=p.appointment_id
        JOIN services s ON s.id=a.service_id
        JOIN doctors d ON d.id=a.doctor_id
        WHERE d.id=? ORDER BY p.id DESC
        """,
        (session["doctor_id"],),
    ).fetchall()
    conn.close()
    stats = {
        "total": len(appointments),
        "pending": sum(x["status"] == "Pending" for x in appointments),
        "confirmed": sum(x["status"] == "Confirmed" for x in appointments),
        "completed": sum(x["status"] == "Completed" for x in appointments),
        "paid": sum(x["payment_status"] == "Paid" for x in appointments),
    }
    return render_template("doctor_dashboard.html", appointments=appointments, payments=payments, stats=stats)


@app.post("/doctor/appointment/<int:appointment_id>/<action>")
@doctor_required
def update_appointment(appointment_id, action):
    status = {"confirm": "Confirmed", "complete": "Completed", "cancel": "Cancelled"}.get(action)
    if not status:
        flash("Invalid appointment action.", "danger")
        return redirect(url_for("doctor_dashboard"))
    conn = get_db()
    conn.execute(
        "UPDATE appointments SET status=? WHERE id=? AND doctor_id=?",
        (status, appointment_id, session["doctor_id"]),
    )
    conn.commit()
    conn.close()
    flash(f"Appointment marked as {status}.", "success")
    return redirect(url_for("doctor_dashboard"))


@app.route("/doctor/database")
@doctor_required
def doctor_database():
    q = request.args.get("q", "").strip()
    conn = get_db()
    like = f"%{q}%"
    patients = conn.execute(
        "SELECT id,name,email,phone,parent_phone,created_at FROM patients "
        "WHERE name LIKE ? OR email LIKE ? OR phone LIKE ? OR parent_phone LIKE ? "
        "ORDER BY id DESC", (like, like, like, like)
    ).fetchall()
    appointments = conn.execute(
        """SELECT a.id,p.name AS patient_name,d.name AS doctor_name,s.name AS service_name,
                  a.appointment_date,a.appointment_time,a.status,a.payment_status,a.reason
           FROM appointments a
           JOIN patients p ON p.id=a.patient_id
           JOIN doctors d ON d.id=a.doctor_id
           JOIN services s ON s.id=a.service_id
           WHERE p.name LIKE ? OR p.email LIKE ? OR a.status LIKE ? OR s.name LIKE ?
           ORDER BY a.id DESC""", (like, like, like, like)
    ).fetchall()
    payments = conn.execute(
        """SELECT pay.id,pay.amount,pay.method,pay.transaction_id,pay.paid_at,
                  pt.name AS patient_name,b.bill_number,s.name AS service_name
           FROM payments pay JOIN patients pt ON pt.id=pay.patient_id
           JOIN bills b ON b.payment_id=pay.id
           JOIN appointments a ON a.id=pay.appointment_id
           JOIN services s ON s.id=a.service_id
           ORDER BY pay.id DESC"""
    ).fetchall()
    conn.close()
    return render_template("database.html", patients=patients, appointments=appointments, payments=payments, q=q)


@app.route("/doctor/database/export/<kind>")
@doctor_required
def export_database(kind):
    import csv, io
    conn = get_db()
    output = io.StringIO()
    writer = csv.writer(output)
    if kind == "patients":
        rows = conn.execute("SELECT id,name,email,phone,parent_phone,created_at FROM patients ORDER BY id").fetchall()
        writer.writerow(["ID","Name","Email","Phone","Parent Phone","Created At"])
        writer.writerows([tuple(r) for r in rows])
    elif kind == "appointments":
        rows = conn.execute("""SELECT a.id,p.name,s.name,a.appointment_date,a.appointment_time,a.status,a.payment_status,a.reason
                              FROM appointments a JOIN patients p ON p.id=a.patient_id JOIN services s ON s.id=a.service_id ORDER BY a.id""").fetchall()
        writer.writerow(["ID","Patient","Service","Date","Time","Status","Payment Status","Reason"])
        writer.writerows([tuple(r) for r in rows])
    elif kind == "payments":
        rows = conn.execute("""SELECT pay.id,pt.name,b.bill_number,pay.amount,pay.method,pay.transaction_id,pay.paid_at
                              FROM payments pay JOIN patients pt ON pt.id=pay.patient_id JOIN bills b ON b.payment_id=pay.id ORDER BY pay.id""").fetchall()
        writer.writerow(["ID","Patient","Bill Number","Amount","Method","Transaction ID","Paid At"])
        writer.writerows([tuple(r) for r in rows])
    else:
        conn.close()
        return "Not found", 404
    conn.close()
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=clinic_{kind}.csv"})


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("home"))


with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
