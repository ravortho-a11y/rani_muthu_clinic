# Rani Muthu Clinic Website

Flask + SQLite clinic website with patient and doctor portals.

## Features
- Rani Muthu Clinic branding and logo
- Patient registration/login
- Doctor login/dashboard
- Appointment booking
- Clinic fees
- Google Pay / UPI payment flow
- Payment transaction/reference storage
- Automatic digital bill/receipt generation
- Patient bill history
- Doctor payment/bill records
- Location QR and Google Maps link
- SQLite database created automatically
- No AI section

## Google Pay setup
Open `app.py` and set the clinic's official UPI ID:

```python
GPAY_UPI_ID = "your-real-upi-id@bank"
```

Do **not** use a placeholder for live payments. The GPay/UPI button will activate only when a real UPI ID is configured.

## Run

```cmd
pip install -r requirements.txt
python app.py
```

Open: `http://127.0.0.1:5000`

## Database
The SQLite file `clinic.db` is created automatically in the project folder. It stores patients, doctors, services, appointments, payments and bills.

## Default doctor login
- Email: `ranimuthuclinic@gmail.com`
- Password: `Doctor@123`

Change the default password before production use.


## Payment and Parent Bill Sharing
- The supplied UPI QR is included as `static/images/gpay_qr.png`.
- QR decodes to UPI ID `7867873401@axl`; verify the bank/UPI account before production.
- Patients can scan the QR in Google Pay/UPI, enter the UPI transaction ID, and receive a database-backed bill.
- Patient registration stores an optional parent/guardian mobile number.
- The bill page includes a WhatsApp share button that sends the bill details and public bill URL. The site must be deployed online for the bill URL to open on the parent's phone.
- Payment confirmation is manual: the patient enters the transaction/reference ID after completing UPI payment.


## Future-ready features included
- Doctor-only Clinic Data Center
- Patient/appointment/payment search
- CSV export for patients, appointments and payments
- SQLite foreign-key validation and stale-session protection
- GPay/UPI QR and payment reference workflow
- Bill/receipt page with parent/guardian WhatsApp sharing

## Production checklist
- Change SECRET_KEY and GPAY_UPI_ID environment variables.
- Use HTTPS and a production WSGI server.
- Verify the clinic's UPI QR/account before accepting real payments.
- Configure WhatsApp sharing according to your clinic's consent/privacy policy.
