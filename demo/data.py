"""Fixed demo data: catalog items, staff and the pools fictional patients are drawn from.

Every person here is made up. Names mix Sinhala, Tamil and Muslim names common in
Sri Lanka; NICs and phone numbers only follow the valid formats.
"""

from datetime import time
from decimal import Decimal

DEPARTMENTS = [
    ("General Medicine", "Outpatient care for adults, chronic disease clinics."),
    ("Cardiology", "Heart and blood pressure clinics."),
    ("Paediatrics", "Children under 14."),
    ("Obstetrics & Gynaecology", "Antenatal care and women's health."),
    ("Orthopaedics", "Bones, joints and fractures."),
    ("Emergency", "Emergency treatment unit and evening OPD."),
]

# Demo logins (username, role, first name, last name). Doctors are created through
# doctors.services with their profile; DOCTORS below holds their profile details.
DEMO_USERS = [
    ("demo.admin", "ADMIN", "Shanika", "Gunawardena"),
    ("demo.doctor", "DOCTOR", "Nuwan", "Jayasinghe"),
    ("demo.doctor2", "DOCTOR", "Fathima", "Rizwan"),
    ("demo.nurse", "NURSE", "Dilani", "Senanayake"),
    ("demo.reception", "RECEPTIONIST", "Kavindi", "Rathnayake"),
    ("demo.lab", "LAB_STAFF", "Tharindu", "Bandara"),
    ("demo.pharmacist", "PHARMACIST", "Selvi", "Arulanandam"),
    ("demo.accountant", "ACCOUNTANT", "Imran", "Hameed"),
]

# Doctors: username, first, last, department, specialization, registration, phone, fee,
# weekly blocks [(weekday 0=Mon, start, end, slot minutes)]. The first two are the
# demo doctor logins; the other four are extra doctors (their logins also use the
# demo password) so the hospital has a realistic timetable.
DOCTORS = [
    (
        "demo.doctor",
        "Nuwan",
        "Jayasinghe",
        "General Medicine",
        "General Physician",
        "SLMC 21457",
        "0712345601",
        Decimal("2500.00"),
        [
            (0, time(8), time(12), 15),
            (2, time(8), time(12), 15),
            (4, time(8), time(12), 15),
            (1, time(16), time(19), 15),
        ],
    ),
    (
        "demo.doctor2",
        "Fathima",
        "Rizwan",
        "Paediatrics",
        "Consultant Paediatrician",
        "SLMC 23981",
        "0772345602",
        Decimal("3000.00"),
        [
            (0, time(9), time(13), 20),
            (1, time(9), time(13), 20),
            (3, time(9), time(13), 20),
            (5, time(8), time(11), 20),
        ],
    ),
    (
        "demo.dr.kumaran",
        "Senthil",
        "Kumaran",
        "Cardiology",
        "Consultant Cardiologist",
        "SLMC 18342",
        "0752345603",
        Decimal("4500.00"),
        [(1, time(14), time(18), 20), (3, time(14), time(18), 20)],
    ),
    (
        "demo.dr.wijesinghe",
        "Chathurika",
        "Wijesinghe",
        "Obstetrics & Gynaecology",
        "Consultant Obstetrician",
        "SLMC 19876",
        "0702345604",
        Decimal("4000.00"),
        [(0, time(14), time(17), 20), (2, time(14), time(17), 20), (4, time(9), time(12), 20)],
    ),
    (
        "demo.dr.fernando",
        "Ruwan",
        "Fernando",
        "Orthopaedics",
        "Orthopaedic Surgeon",
        "SLMC 20513",
        "0762345605",
        Decimal("3500.00"),
        [(2, time(15), time(18), 15), (4, time(15), time(18), 15), (5, time(9), time(12), 15)],
    ),
    (
        "demo.dr.ashraf",
        "Mohamed",
        "Ashraf",
        "Emergency",
        "Emergency Physician",
        "SLMC 24670",
        "0782345606",
        Decimal("2000.00"),
        [(day, time(18), time(21), 15) for day in range(7)],
    ),
]

# Medicines: name, generic, form, strength, unit price, reorder level, stock plan.
# Stock plans: "plenty" (prescribed every day), "low" (below the reorder level),
# "expiring" (a batch expiring within 90 days), "expired" (an expired batch still on
# the shelf), "none" (never received: out of stock).
MEDICINES = [
    ("Paracetamol", "Paracetamol", "TABLET", "500 mg", "5.00", 200, "plenty"),
    ("Amoxicillin", "Amoxicillin", "CAPSULE", "500 mg", "25.00", 100, "plenty"),
    ("Cetirizine", "Cetirizine", "TABLET", "10 mg", "8.00", 100, "plenty"),
    ("Amlodipine", "Amlodipine", "TABLET", "5 mg", "12.00", 150, "plenty"),
    ("Losartan", "Losartan potassium", "TABLET", "50 mg", "18.00", 150, "plenty"),
    ("Metformin", "Metformin", "TABLET", "500 mg", "6.00", 200, "plenty"),
    ("Gliclazide", "Gliclazide", "TABLET", "80 mg", "10.00", 100, "expiring"),
    ("Omeprazole", "Omeprazole", "CAPSULE", "20 mg", "15.00", 150, "plenty"),
    ("Domperidone", "Domperidone", "TABLET", "10 mg", "7.00", 100, "plenty"),
    ("Diclofenac", "Diclofenac sodium", "TABLET", "50 mg", "9.00", 100, "plenty"),
    ("Nitrofurantoin", "Nitrofurantoin", "CAPSULE", "100 mg", "30.00", 60, "plenty"),
    ("Ciprofloxacin", "Ciprofloxacin", "TABLET", "500 mg", "28.00", 60, "expiring"),
    ("Oral rehydration salts", "ORS", "OTHER", "20.5 g sachet", "20.00", 100, "plenty"),
    ("Metronidazole", "Metronidazole", "TABLET", "400 mg", "11.00", 80, "plenty"),
    ("Salbutamol inhaler", "Salbutamol", "INHALER", "100 mcg/dose", "650.00", 10, "low"),
    ("Atorvastatin", "Atorvastatin", "TABLET", "20 mg", "22.00", 100, "plenty"),
    ("Aspirin", "Acetylsalicylic acid", "TABLET", "75 mg", "3.00", 200, "plenty"),
    ("Paracetamol syrup", "Paracetamol", "SYRUP", "120 mg/5 ml", "180.00", 30, "plenty"),
    (
        "Amoxicillin suspension",
        "Amoxicillin",
        "SUSPENSION",
        "125 mg/5 ml",
        "320.00",
        20,
        "expiring",
    ),
    ("Folic acid", "Folic acid", "TABLET", "5 mg", "2.50", 150, "plenty"),
    ("Ferrous sulphate", "Ferrous sulphate", "TABLET", "200 mg", "4.00", 150, "low"),
    ("Insulin glargine", "Insulin glargine", "INJECTION", "100 IU/ml", "2850.00", 10, "low"),
    ("Vitamin B complex", "Vitamin B complex", "TABLET", "Standard", "3.00", 100, "expired"),
    ("Chlorpheniramine", "Chlorpheniramine maleate", "TABLET", "4 mg", "2.00", 100, "plenty"),
    ("Hydrocortisone cream", "Hydrocortisone", "CREAM", "1%", "240.00", 15, "none"),
]

# Lab tests: code, name, section, specimen, price, turnaround hours, parameters.
# Parameter: (name, unit, low, high) for numbers or (name, None, None, ref_text) for text.
LAB_TESTS = [
    (
        "FBC",
        "Full blood count",
        "HAEMATOLOGY",
        "BLOOD",
        "1200.00",
        4,
        [
            ("Haemoglobin", "g/dL", "12", "16"),
            ("WBC", "10^9/L", "4", "11"),
            ("Platelets", "10^9/L", "150", "400"),
        ],
    ),
    (
        "FBS",
        "Fasting blood sugar",
        "BIOCHEMISTRY",
        "PLASMA",
        "450.00",
        4,
        [
            ("Glucose (fasting)", "mg/dL", "70", "100"),
        ],
    ),
    (
        "LIPID",
        "Lipid profile",
        "BIOCHEMISTRY",
        "SERUM",
        "2200.00",
        24,
        [
            ("Total cholesterol", "mg/dL", None, "200"),
            ("Triglycerides", "mg/dL", None, "150"),
            ("HDL cholesterol", "mg/dL", "40", None),
            ("LDL cholesterol", "mg/dL", None, "130"),
        ],
    ),
    (
        "LFT",
        "Liver function tests",
        "BIOCHEMISTRY",
        "SERUM",
        "2600.00",
        24,
        [
            ("ALT", "U/L", "7", "56"),
            ("AST", "U/L", "10", "40"),
            ("ALP", "U/L", "44", "147"),
            ("Total bilirubin", "mg/dL", "0.1", "1.2"),
        ],
    ),
    (
        "SCR",
        "Serum creatinine",
        "BIOCHEMISTRY",
        "SERUM",
        "650.00",
        6,
        [
            ("Creatinine", "mg/dL", "0.6", "1.3"),
        ],
    ),
    (
        "UFR",
        "Urine full report",
        "URINALYSIS",
        "URINE",
        "500.00",
        4,
        [
            ("Protein", None, None, "Nil"),
            ("Sugar", None, None, "Nil"),
            ("Pus cells", "/HPF", "0", "5"),
            ("Red cells", "/HPF", "0", "3"),
        ],
    ),
    (
        "HBA1C",
        "HbA1c",
        "BIOCHEMISTRY",
        "BLOOD",
        "2400.00",
        48,
        [
            ("HbA1c", "%", "4", "5.6"),
        ],
    ),
    (
        "TSH",
        "Thyroid stimulating hormone",
        "IMMUNOLOGY",
        "SERUM",
        "2800.00",
        48,
        [
            ("TSH", "mIU/L", "0.4", "4"),
        ],
    ),
    (
        "CRP",
        "C-reactive protein",
        "IMMUNOLOGY",
        "SERUM",
        "1500.00",
        6,
        [
            ("CRP", "mg/L", None, "5"),
        ],
    ),
    (
        "UCUL",
        "Urine culture",
        "MICROBIOLOGY",
        "URINE",
        "1800.00",
        72,
        [
            ("Culture", None, None, "No growth"),
            ("Sensitivity", None, None, "Not applicable"),
        ],
    ),
]

# Diagnoses used for completed visits: ICD-10 code, description, medicines
# [(medicine name, dose, frequency, days, quantity)], lab tests that may be ordered.
DIAGNOSES = [
    (
        "J06.9",
        "Acute upper respiratory infection",
        [
            ("Paracetamol", "1 tablet", "TDS", 3, 9),
            ("Cetirizine", "1 tablet", "NOCTE", 5, 5),
            ("Amoxicillin", "1 capsule", "TDS", 5, 15),
        ],
        ["FBC"],
    ),
    (
        "I10",
        "Essential hypertension",
        [("Amlodipine", "1 tablet", "OD", 30, 30), ("Losartan", "1 tablet", "OD", 30, 30)],
        ["LIPID", "SCR"],
    ),
    (
        "E11.9",
        "Type 2 diabetes mellitus without complications",
        [("Metformin", "1 tablet", "BD", 30, 60), ("Gliclazide", "1 tablet", "OD", 30, 30)],
        ["FBS", "HBA1C"],
    ),
    (
        "K29.7",
        "Gastritis",
        [("Omeprazole", "1 capsule", "OD", 14, 14), ("Domperidone", "1 tablet", "TDS", 5, 15)],
        [],
    ),
    (
        "M54.5",
        "Low back pain",
        [("Diclofenac", "1 tablet", "BD", 5, 10), ("Paracetamol", "2 tablets", "TDS", 5, 30)],
        [],
    ),
    (
        "N39.0",
        "Urinary tract infection",
        [("Nitrofurantoin", "1 capsule", "QDS", 5, 20), ("Paracetamol", "1 tablet", "TDS", 3, 9)],
        ["UFR", "UCUL"],
    ),
    (
        "A09",
        "Acute gastroenteritis",
        [
            ("Oral rehydration salts", "1 sachet", "PRN", 3, 6),
            ("Metronidazole", "1 tablet", "TDS", 5, 15),
        ],
        ["FBC"],
    ),
    ("R50.9", "Fever, unspecified", [("Paracetamol", "1 tablet", "QDS", 3, 12)], ["FBC", "CRP"]),
]

COMPLAINTS = {
    "J06.9": "Cough, sore throat and runny nose for 3 days.",
    "I10": "Review of blood pressure. Occasional headaches.",
    "E11.9": "Diabetes clinic review. Feels well.",
    "K29.7": "Burning epigastric pain after meals for 2 weeks.",
    "M54.5": "Lower back pain after lifting a heavy load.",
    "N39.0": "Burning on passing urine and frequency for 2 days.",
    "A09": "Loose stools and vomiting since yesterday.",
    "R50.9": "Fever for 2 days with body aches.",
}

TOWNS = [
    "Colombo 05",
    "Dehiwala",
    "Moratuwa",
    "Kandy",
    "Peradeniya",
    "Galle",
    "Matara",
    "Kurunegala",
    "Negombo",
    "Gampaha",
    "Jaffna",
    "Batticaloa",
    "Trincomalee",
    "Kalmunai",
    "Anuradhapura",
    "Badulla",
    "Ratnapura",
    "Kegalle",
    "Puttalam",
    "Nuwara Eliya",
]
STREETS = [
    "Temple Road",
    "Station Road",
    "Main Street",
    "Lake Drive",
    "Hospital Road",
    "Church Lane",
    "Kovil Road",
    "Mosque Road",
    "Galle Road",
    "School Lane",
    "Hill Street",
]

# Fictional people: (first name, gender) pools and surnames per community.
SINHALA = (
    [
        ("Kasun", "MALE"),
        ("Chamara", "MALE"),
        ("Sunil", "MALE"),
        ("Pradeep", "MALE"),
        ("Lahiru", "MALE"),
        ("Asanka", "MALE"),
        ("Nimal", "MALE"),
        ("Dinesh", "MALE"),
        ("Sanduni", "FEMALE"),
        ("Ishara", "FEMALE"),
        ("Nadeesha", "FEMALE"),
        ("Malini", "FEMALE"),
        ("Chathuri", "FEMALE"),
        ("Dulani", "FEMALE"),
        ("Hiruni", "FEMALE"),
        ("Kumari", "FEMALE"),
    ],
    [
        "Perera",
        "Silva",
        "Fernando",
        "Wickramasinghe",
        "Dissanayake",
        "Herath",
        "Karunaratne",
        "Weerasinghe",
        "Abeysekara",
        "Gamage",
        "Liyanage",
        "Samarasinghe",
    ],
)
TAMIL = (
    [
        ("Kannan", "MALE"),
        ("Suresh", "MALE"),
        ("Ravi", "MALE"),
        ("Vimal", "MALE"),
        ("Thushanthi", "FEMALE"),
        ("Kavitha", "FEMALE"),
        ("Nirosha", "FEMALE"),
        ("Priya", "FEMALE"),
    ],
    ["Sivakumar", "Rajendran", "Nadarajah", "Thevarajah", "Ganeshan", "Selvaraj"],
)
MUSLIM = (
    [
        ("Rizwan", "MALE"),
        ("Imtiaz", "MALE"),
        ("Faizal", "MALE"),
        ("Nazeer", "MALE"),
        ("Fathima", "FEMALE"),
        ("Shifana", "FEMALE"),
        ("Rishna", "FEMALE"),
        ("Ayesha", "FEMALE"),
    ],
    ["Mohamed", "Hameed", "Fawzan", "Ismail", "Careem", "Jaleel"],
)
CHILD_NAMES = [
    ("Senuli", "Perera", "FEMALE"),
    ("Thevin", "Silva", "MALE"),
    ("Aashik", "Hameed", "MALE"),
    ("Yalini", "Sivakumar", "FEMALE"),
]

# Extra employees (no login): first, last, designation, category, department, gender.
EXTRA_STAFF = [
    ("Ruwani", "Kumarasinghe", "Nursing Sister", "NURSING", "General Medicine", "FEMALE"),
    ("Sajith", "Premadasa", "Staff Nurse", "NURSING", "Emergency", "MALE"),
    ("Tharshini", "Yogarajah", "Staff Nurse", "NURSING", "Paediatrics", "FEMALE"),
    ("Hasini", "Ekanayake", "Midwife", "NURSING", "Obstetrics & Gynaecology", "FEMALE"),
    (
        "Mahesh",
        "Jayaweera",
        "Medical Laboratory Technologist",
        "ALLIED_HEALTH",
        "General Medicine",
        "MALE",
    ),
    ("Nazrin", "Mohideen", "Pharmacy Assistant", "ALLIED_HEALTH", "General Medicine", "FEMALE"),
    ("Upul", "Rajapaksha", "Radiographer", "ALLIED_HEALTH", "Orthopaedics", "MALE"),
    (
        "Gayani",
        "Madushani",
        "Medical Records Officer",
        "ADMINISTRATIVE",
        "General Medicine",
        "FEMALE",
    ),
    ("Saman", "Kumara", "Hospital Attendant", "SUPPORT", "Emergency", "MALE"),
    ("Anton", "Joseph", "Porter", "SUPPORT", "General Medicine", "MALE"),
    ("Sumana", "Rani", "Cleaning Supervisor", "SUPPORT", "General Medicine", "FEMALE"),
    ("Krishanthan", "Paramanathan", "Security Officer", "SUPPORT", "Emergency", "MALE"),
]

# The employee record for each demo login: designation, category, department.
DEMO_EMPLOYEES = {
    "demo.admin": ("Hospital Administrator", "ADMINISTRATIVE", "General Medicine"),
    "demo.doctor": ("Consultant Physician", "MEDICAL", "General Medicine"),
    "demo.doctor2": ("Consultant Paediatrician", "MEDICAL", "Paediatrics"),
    "demo.nurse": ("Staff Nurse", "NURSING", "General Medicine"),
    "demo.reception": ("Receptionist", "ADMINISTRATIVE", "General Medicine"),
    "demo.lab": ("Medical Laboratory Technologist", "ALLIED_HEALTH", "General Medicine"),
    "demo.pharmacist": ("Pharmacist", "ALLIED_HEALTH", "General Medicine"),
    "demo.accountant": ("Accountant", "ADMINISTRATIVE", "General Medicine"),
}

WARDS = [
    ("General Ward A", "GENERAL", "General Medicine", Decimal("3500.00"), 10),
    ("ICU", "ICU", "Emergency", Decimal("25000.00"), 4),
    ("Maternity", "MATERNITY", "Obstetrics & Gynaecology", Decimal("6000.00"), 6),
]

ADMISSION_REASONS = [
    (
        "Community-acquired pneumonia",
        "Treated with IV antibiotics; improved. Complete oral course.",
    ),
    (
        "Dengue fever with warning signs",
        "Platelets recovered; afebrile 48 hours. Review in 1 week.",
    ),
    (
        "Uncontrolled diabetes with dehydration",
        "Glucose controlled on insulin. Diabetes clinic in 2 weeks.",
    ),
    (
        "Acute gastroenteritis with dehydration",
        "Rehydrated, tolerating oral fluids. Home with ORS.",
    ),
    ("Chest pain for observation", "ACS excluded. Cardiology clinic review arranged."),
    ("Normal vaginal delivery", "Mother and baby well. Postnatal clinic in 6 weeks."),
    ("Fracture neck of femur", "Surgery done, mobilising with frame. Physiotherapy follow-up."),
    (
        "Acute exacerbation of asthma",
        "Nebulised and steroids; peak flow normal. Inhaler technique taught.",
    ),
    ("Cellulitis of the leg", "IV antibiotics, swelling settled. Oral course to complete."),
    ("Severe anaemia for transfusion", "Transfused 2 units. Iron supplements and review."),
]
