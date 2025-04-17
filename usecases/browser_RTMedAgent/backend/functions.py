from typing import Any, Dict, Optional, TypedDict
import json

# ------------------------------------------
# Simulated Internal Data ("Databases")
# ------------------------------------------

# Patients and their basic information
patients_db: Dict[str, Dict[str, str]] = {
    "Alice Brown": {"dob": "1987-04-12", "patient_id": "P54321", "phone": "555-4321"},
    "Bob Johnson": {"dob": "1992-11-25", "patient_id": "P98765", "phone": "555-8765"},
    "Charlie Davis": {"dob": "1980-01-15", "patient_id": "P11223", "phone": "555-1122"},
    "Diana Evans": {"dob": "1995-07-08", "patient_id": "P33445", "phone": "555-3344"},
    "Ethan Foster": {"dob": "1983-03-22", "patient_id": "P55667", "phone": "555-5566"},
    "Fiona Green": {"dob": "1998-09-10", "patient_id": "P77889", "phone": "555-7788"},
    "George Harris": {"dob": "1975-12-05", "patient_id": "P99001", "phone": "555-9900"},
    "Hannah Irving": {"dob": "1989-06-30", "patient_id": "P22334", "phone": "555-2233"},
    "Ian Jackson": {"dob": "1993-02-18", "patient_id": "P44556", "phone": "555-4455"},
    "Julia King": {"dob": "1986-08-14", "patient_id": "P66778", "phone": "555-6677"},
}

# Patient medications and refill info
prescriptions_db: Dict[str, Dict[str, Dict[str, str]]] = {
    "Alice Brown": {"Metformin": {"last_refill": "2024-03-01", "pharmacy": "City Pharmacy"}},
    "Bob Johnson": {"Atorvastatin": {"last_refill": "2024-02-20", "pharmacy": "Town Pharmacy"}},
    "Charlie Davis": {"Lisinopril": {"last_refill": "2024-01-15", "pharmacy": "Central Pharmacy"}},
    "Diana Evans": {"Omeprazole": {"last_refill": "2024-03-05", "pharmacy": "East Pharmacy"}},
    "Ethan Foster": {"Amlodipine": {"last_refill": "2024-02-28", "pharmacy": "West Pharmacy"}},
    "Fiona Green": {"Levothyroxine": {"last_refill": "2024-03-10", "pharmacy": "North Pharmacy"}},
    "George Harris": {"Simvastatin": {"last_refill": "2024-01-25", "pharmacy": "South Pharmacy"}},
    "Hannah Irving": {"Losartan": {"last_refill": "2024-02-15", "pharmacy": "Downtown Pharmacy"}},
    "Ian Jackson": {"Hydrochlorothiazide": {"last_refill": "2024-03-12", "pharmacy": "Uptown Pharmacy"}},
    "Julia King": {"Gabapentin": {"last_refill": "2024-03-08", "pharmacy": "Suburban Pharmacy"}},
}

# Medication information
medications_info_db: Dict[str, str] = {
    "Metformin": "Metformin is used to treat type 2 diabetes. Common side effects include nausea and diarrhea.",
    "Atorvastatin": "Atorvastatin is used to lower cholesterol. Side effects may include muscle pain and digestive issues.",
    "Lisinopril": "Lisinopril is used to treat high blood pressure. Side effects may include dizziness and dry cough.",
    "Omeprazole": "Omeprazole is used to treat acid reflux. Side effects may include headache and abdominal pain.",
    "Amlodipine": "Amlodipine is used to treat high blood pressure. Side effects may include swelling and fatigue.",
    "Levothyroxine": "Levothyroxine is used to treat hypothyroidism. Side effects may include weight loss and heat sensitivity.",
    "Simvastatin": "Simvastatin is used to lower cholesterol. Side effects may include muscle pain and liver issues.",
    "Losartan": "Losartan is used to treat high blood pressure. Side effects may include dizziness and back pain.",
    "Hydrochlorothiazide": "Hydrochlorothiazide is used to treat fluid retention. Side effects may include increased urination and dizziness.",
    "Gabapentin": "Gabapentin is used to treat nerve pain. Side effects may include drowsiness and dizziness.",
}

# ---------------------------------------------------------------------------
# TypedDict argument models  (one‑to‑one with tools.py schemas)
# ---------------------------------------------------------------------------
class AuthenticateArgs(TypedDict):
    first_name: str
    last_name: str
    phone_number: str

class ScheduleAppointmentArgs(TypedDict, total=False):
    patient_name: str
    dob: str
    appointment_type: str
    preferred_date: str
    preferred_time: str

class RefillPrescriptionArgs(TypedDict, total=False):
    patient_name: str
    medication_name: str
    pharmacy: str

class LookupMedicationArgs(TypedDict):
    medication_name: str

class PAArgs(TypedDict):
    patient_info: Dict[str, Any]
    physician_info: Dict[str, Any]
    clinical_info: Dict[str, Any]
    treatment_plan: Dict[str, Any]
    policy_text: str

class EscalateEmergencyArgs(TypedDict):
    reason: str

# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------
def _json(ok: bool, msg: str, **data):
    return json.dumps({"ok": ok, "message": msg, "data": data or None}, ensure_ascii=False)

# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------
async def authenticate_user(args: AuthenticateArgs) -> str:
    first = args["first_name"].strip().title()
    last  = args["last_name"].strip().title()
    phone = args["phone_number"].strip()
    full  = f"{first} {last}"

    rec = patients_db.get(full)
    if rec and rec["phone"].replace("-","")[-7:] == phone[-7:]:
        return _json(True, f"Authenticated {full}.", patient_id=rec["patient_id"])
    return _json(False, "Authentication failed – name or phone mismatch.")

# ---------------------------------------------------------------------------
async def schedule_appointment(args: ScheduleAppointmentArgs) -> str:
    name = args["patient_name"]
    dob  = args["dob"]
    appt = args["appointment_type"]
    date = args.get("preferred_date") or str(_dt.date.today() + _dt.timedelta(days=3))
    time = args.get("preferred_time", "14:00")

    rec = patients_db.get(name)
    if not rec or rec["dob"] != dob:
        return _json(False, "Patient not found or DOB mismatch.")

    return _json(True, f"Appointment booked for {name} on {date} at {time}.", date=date, time=time, appointment_type=appt)

# ---------------------------------------------------------------------------
async def refill_prescription(args: RefillPrescriptionArgs) -> str:
    name = args["patient_name"]
    med  = args["medication_name"]
    pharm= args.get("pharmacy")

    rx = prescriptions_db.get(name, {})
    if med not in rx:
        return _json(False, f"No active prescription for {med} under {name}.")

    pharmacy = pharm or rx[med]["pharmacy"]
    return _json(True, f"Refill placed for {med} to {pharmacy}.", pharmacy=pharmacy, medication=med)

# ---------------------------------------------------------------------------
async def lookup_medication_info(args: LookupMedicationArgs) -> str:
    med = args["medication_name"]
    info = medications_info_db.get(med)
    if not info:
        return _json(False, f"Medication {med} not found.")
    return _json(True, f"Information on {med}.", summary=info)

# ---------------------------------------------------------------------------
async def evaluate_prior_authorization(args: PAArgs) -> str:
    patient = args["patient_info"].get("patient_name", "Unknown")
    med     = args["treatment_plan"].get("requested_medication", "unknown medication")
    if med == "unknown medication":
        return _json(False, "Requested medication missing.")
    return _json(True, f"Prior authorization for {med} auto‑approved.")

# ---------------------------------------------------------------------------
async def escalate_emergency(args: EscalateEmergencyArgs) -> str:
    return _json(True, "Emergency escalation triggered.", reason=args["reason"])
