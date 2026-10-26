# Health Record Import

MediKeep can import the clinical summary that patient portals hand out, so a
record that already exists at your hospital does not have to be typed in again.
The importer reads:

- a C-CDA XML document (a Continuity of Care Document, the HL7 format most US
  portals use for "Download my record"),
- the zip file Epic MyChart produces for that download (an IHE XDM archive; you
  do not need to unpack it), or
- a FHIR R4 Bundle in JSON, for records that come from another tool.

The import adds records to a patient that already exists in MediKeep. It never
creates a patient.

## Downloading your record from MyChart

1. Sign in to MyChart in a browser (the mobile app does not offer the download).
2. Open the menu and look for **Document Center**, then **Download My Record**
   (some organizations label it "Request Records" or "Visit Records").
3. Choose the date range or the visits you want. The full record is usually the
   most useful choice for a first import.
4. Pick the **Download** option that produces a machine-readable file. MyChart
   saves a zip file, typically named something like `MyChart_Export.zip`, with
   a `MachineReadable_XDMFormat` folder inside. Leave it zipped.

Other portals built on Epic, Cerner, athenahealth or Meditech offer the same
"Download" or "Transmit" function; any of them that produce a `.xml` C-CDA
document will work.

## Importing

1. Make sure the patient you want to import into is the active patient.
2. Open **Export Records** in the sidebar and use **Import health record**.
3. Choose the file. The size limit is 50 MB.
4. Review the preview. It lists, per record type, how many rows would be new
   and how many would update rows from an earlier import, the sections found in
   the document, anything that will not be imported and why, and the name and
   birth date of the patient described in the file so you can confirm you picked
   the right patient.
5. Confirm. The result screen repeats the counts for what was created, updated
   and skipped.

If the name or birth date in the file does not match the selected patient, the
preview shows a warning. The import still goes ahead for the patient you
selected, and the patient's own details are never changed by an import.

## What gets imported

| Section in the file | MediKeep records |
|---|---|
| Problems | Conditions, with ICD-10 and SNOMED codes, onset and resolution dates and status |
| Allergies | Allergies, with the reaction, severity, onset date and status |
| Medications | Medications, with dose, frequency, route, start and end dates and status; the full sig text is kept in the notes |
| Immunizations | Immunizations, with date, lot number, manufacturer, route, site and dose number |
| Procedures | Procedures, with code, date, status and facility |
| Encounters | Visits, with reason, type, date, location and duration |
| Vital Signs | Vitals, one row per measurement time: blood pressure, heart rate, temperature, weight, height, oxygen saturation, respiratory rate, blood glucose, BMI and pain score, converted to the units MediKeep stores (Fahrenheit, pounds, inches, mg/dL) |
| Results | Lab results, one per report, with each test as a component: value, unit, reference range, flag and the matched standardized test name used for trends |

Each imported row records where it came from (`import_source`) and the
identifier it had in the file (`external_id`). These are shown read-only in
the API and are what make re-importing safe.

## What does not get imported

- "No known allergies" entries, refused or not-given immunizations, and anything
  marked entered-in-error in the source.
- Social history (smoking, alcohol), plan of treatment, care team, insurance,
  imaging studies, family history, goals and notes. The preview lists these
  sections with their entry counts so you know they were in the file.
- The organization, document and provenance resources that describe the file
  itself.
- A measurement or value the file does not actually contain (for example a lab
  test listed with "unknown" instead of a value), or one whose unit MediKeep
  cannot convert. Each is listed in the preview under "Not imported" with the
  reason.
- Any row that MediKeep's own validation rejects (for example a date in the
  future). The reason is listed in the preview and the rest of the file is
  still imported.

Some source fields do not map one to one. Medication names longer than 100
characters and dose or frequency text longer than 50 characters are shortened,
with the full text kept in the notes. Allergies that carry no severity are
stored with severity "none". Visits longer than ten hours have their length
recorded in the notes rather than in the duration field. Encounter status is
noted in the visit notes when it is anything other than finished.

## Re-importing

Records are matched on the identifier in the file, scoped to the patient. When
you import a newer download from the same portal:

- rows that are new in the file are created,
- rows that already exist are updated with the file's values, and
- nothing is duplicated.

Tags you added to a record and links you made to practitioners, pharmacies or
conditions are kept on update. Other fields, including notes, take the values
from the file, so edits you made to an imported record's own fields are
replaced on re-import. If you would rather keep every existing row as it is,
untick **Update records imported earlier** on the preview; existing rows are
then skipped and only new ones are added.

Vitals are matched on their measurement time rather than on a single source
identifier, so a later download that adds another measurement at the same
time, such as a weight taken alongside an earlier blood pressure reading, is
added to the same row.

Importing a FHIR Bundle that was produced from the same C-CDA document (for
example by running it through healthrecord-bridge yourself) matches the same
rows, because the identifiers are derived from the document rather than
generated at random.

Each import writes one entry to the activity log with the counts.
