# Health Record Import

MediKeep can import the clinical summary your patient portal lets you download, so records that already exist at your hospital do not have to be typed in again.

---

## Overview

The importer reads three kinds of file:

- A **C-CDA XML document** (Continuity of Care Document), the HL7 format most US portals use for "Download my record"
- The **zip file Epic MyChart saves** for that download (an IHE XDM archive). Upload it as is.
- A **FHIR R4 Bundle** in JSON, for records that come from another tool

Records are added to a patient that already exists in MediKeep. The import never creates a patient.

---

## Downloading Your Record from MyChart

### Step 1: Sign In on the Web

Sign in to MyChart in a browser. The mobile app does not offer the download.

### Step 2: Open Download My Record

Open the menu and go to **Document Center**, then **Download My Record**. Some organizations call it **Request Records** or **Visit Records**.

### Step 3: Choose the Date Range

Pick the visits or the date range you want. For a first import, choose the full record.

### Step 4: Download the Machine-Readable File

Pick the **Download** option that produces a machine-readable file. MyChart saves a zip file with a `MachineReadable_XDMFormat` folder inside. Leave it zipped.

Portals built on Epic, Cerner, athenahealth or Meditech offer the same **Download** or **Transmit** function. Any of them that produce a `.xml` C-CDA document work with the importer.

---

## How to Import

### Step 1: Select the Patient

Make the patient you want to import into the active patient.

### Step 2: Open the Importer

Go to **Export Records** in the sidebar and click **Import health record**.

### Step 3: Choose the File

Select the `.xml`, `.zip` or `.json` file. The size limit is 50 MB.

### Step 4: Review the Preview

The preview shows:

| Section | What it tells you |
|---------|-------------------|
| **Counts** | Per record type, how many rows are new and how many update rows from an earlier import |
| **Patient in file** | The name and birth date of the patient the file describes, so you can confirm you picked the right patient |
| **Sections in the file** | Each section the document contains, with entry counts |
| **Not imported** | Anything MediKeep does not import, grouped by reason |
| **Warnings** | Differences between the file and the selected patient |

If the name or birth date in the file does not match the selected patient, the preview shows a warning. The import still goes ahead for the patient you selected. An import never changes the patient's own details.

### Step 5: Import

Click **Import**. The result screen repeats the counts for what was created, updated and skipped.

---

## What Gets Imported

| Section in the file | MediKeep records |
|---------------------|------------------|
| **Problems** | Conditions, with ICD-10 and SNOMED codes, onset and resolution dates and status |
| **Allergies** | Allergies, with reaction, severity, onset date and status |
| **Medications** | Medications, with dose, frequency, route, start and end dates and status. The full sig text is kept in the notes |
| **Immunizations** | Immunizations, with date, lot number, manufacturer, route, site and dose number |
| **Procedures** | Procedures, with code, date, status and facility |
| **Encounters** | Visits, with reason, type, date, location and duration |
| **Vital Signs** | Vitals, one row per measurement time: blood pressure, heart rate, temperature, weight, height, oxygen saturation, respiratory rate, blood glucose, BMI and pain score, converted to Fahrenheit, pounds, inches and mg/dL |
| **Results** | Lab results, one per report, with each test as a component: value, unit, reference range, flag and the standardized test name used for trends |

Each imported row records where it came from (`import_source`) and the identifier it had in the file (`external_id`). Both are shown read-only in the API.

### Field limits

Medication names longer than 100 characters and dose or frequency text longer than 50 characters are shortened, with the full text kept in the notes. Allergies without a severity are stored with severity "none". Visits longer than ten hours have their length written to the notes instead of the duration field. An encounter status other than finished is written to the visit notes.

---

## What Is Not Imported

- "No known allergies" entries, refused or not-given immunizations, and anything marked entered-in-error in the source
- Social history (smoking, alcohol), plan of treatment, care team, insurance, imaging studies, family history, goals and notes. The preview lists these sections with their entry counts
- The organization, document and provenance resources that describe the file itself
- A result the file lists without a value (for example a lab test marked "unknown"), or a measurement in a unit MediKeep cannot convert. Each is listed in the preview under **Not imported** with the reason
- Any row MediKeep's own validation rejects, for example a date in the future. The reason is listed in the preview and the rest of the file is imported

---

## Re-importing

Rows are matched on the identifier in the file, scoped to the patient. When you import a newer download from the same portal:

- Rows that are new in the file are created
- Rows that already exist are updated with the file's values
- No row is created twice

Tags you added to a record and links you made to practitioners, pharmacies or conditions are kept on update. Every other field, including notes, takes the value from the file, so edits to an imported record's own fields are replaced on re-import. To keep every existing row as it is, untick **Update records imported earlier** on the preview. Existing rows are then skipped and only new ones are added.

Vitals are matched on their measurement time. A later download that adds a measurement at the same time, such as a weight taken alongside a blood pressure reading already imported, is added to that row.

A FHIR Bundle produced from the same C-CDA document (for example with healthrecord-bridge) matches the same rows, because the identifiers come from the document itself.

Each import writes one entry to the activity log with the counts.
