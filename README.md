# StrokeKey

**Dynamic Signature Research Prototype** — a new, local Python implementation
for capturing and studying handwritten signature dynamics.

**Research prototype - not for production authentication.** It is not a secure
password replacement, and an ACCEPTED result is only an experimental similarity
decision. No password, authentication credential, or cryptographic proof is created.
This project does not use or reconstruct proprietary PenOp code, formats,
interfaces, algorithms, datasets, or branding.

## Static versus dynamic signatures

Static verification compares an image of handwriting. Dynamic verification studies
the ordered motion that produced it: coordinates, timing, pen lifts, speed, and
available sensor measurements. StrokeKey treats the **time series as the primary
record**. Plots are views of that record, not the stored biometric template.

## Installation

Use Python **3.11 or newer**. In PowerShell at the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If a PyCharm virtual environment already exists, select that interpreter and skip
creating another one. Activation is optional; you can invoke
`.\.venv\Scripts\python.exe` directly if PowerShell activation is restricted.
The dependencies are PySide6, NumPy, matplotlib, and pytest. SciPy is not needed
by the initial algorithms. Package installation may download dependencies;
**the running application has no network functionality**.

## Running

```powershell
python main.py
```

In PyCharm, run `main.py` with the project's interpreter. A native desktop window
opens with Capture Sample, Enroll User, Verify Signature, View Samples, and Exit.
The database is created automatically at `data/strokekey.db`, resolved relative
to the source project rather than the current working directory. The source
installation must have a writable `data` directory. No server is needed.

## Capturing

Choose **Capture Sample**, then draw in the white area. Pressing starts a stroke;
releasing ends it. Separate strokes are never joined in the live drawing. The
status shows points, strokes, and measured duration. **Clear** discards the current
unsaved drawing; **Save Sample** validates and saves raw measurements locally.
The initial validity rules require 20 points, 0.250 seconds, at least one stroke,
and 2 pixels of extent along at least one axis. Horizontal signatures are allowed.
Accidental clicks and interrupted captures are rejected with helpful messages.
The memory limit is 50,000 points. Adjust parameters in `stroke_key/config.py`.

Elapsed timing uses a monotonic clock at event receipt. It includes inter-stroke
gaps but excludes idle time after the final release. Event sampling depends on
the OS, Qt, device, and driver; these are not hardware sensor timestamps.

## Enrollment

Choose **Enroll User**, enter a research participant name, and capture **five**
valid samples. Each **Save Sample** writes an unassigned draft immediately and
shows its point count, duration, and strokes. Select a saved sample and choose
**Retry selected saved sample** to delete that draft and draw a replacement.
After five samples, choose **Complete Enrollment**. The identity, associations,
and duration/path/stroke statistics are committed transactionally.

Closing early retains unassigned drafts, visible in View Samples; they are not
verification references and are not automatically resumed in a later enrollment.
Names need not be unique; short user IDs distinguish them in the verification list.
The application ships with no enrolled identities or real biometric demo data.

## Verification

Choose **Verify Signature**, select an enrolled user, sign, and press **Verify**.
All valid enrollment references are compared. The result shows median overall
similarity, experimental ACCEPTED/REJECTED, DTW, duration, geometry, and stroke
similarities, plus each reference's overall/DTW scores, durations and stroke counts.
No enrolled users produces an explanatory empty state. Missing or malformed
references cause a visible error rather than silent acceptance. Verification
candidates are not saved automatically. Comparisons run in a worker thread.

## Inspecting samples

**View Samples** lists persisted records, including enrollment drafts. Select one
to inspect the raw signature, sample/user/device metadata, point/stroke counts,
duration, path length, velocity over time, and pressure when available. Paths
exclude pen-up jumps; the coordinate plot preserves separate strokes. Velocity
is measured between consecutive points within a stroke. Missing pressure is
explicitly displayed. Unreadable records produce a friendly message.

### Analyze Sample

The viewer now has **Raw sample**, **Analyze Sample**, and **Record metadata** tabs.
For an enrolled sample, analysis automatically uses its participant's enrollment
references and excludes the selected sample. For an unassigned capture or draft,
choose an **Enrollment profile** to compare it to. Choose an **Overlay** reference
or **No overlay**. The normalized view colours strokes by speed, outlines pause
starts, and shades detected pause intervals on the speed plot. Stroke boundaries
are preserved in both views.

**Feature comparisons** shows the selected measurement, reference median, observed
minimum/maximum, contributing reference count, and whether the measurement falls
inside or outside that range. **Measured explanations** describes the differences;
**Capture quality** reports sampling intervals, gaps, equal-time segments and sensor
coverage. These are descriptive statistics, not a trained AI model, a calibrated
confidence score, or evidence of authenticity. At least two eligible references
are required for range comparisons. Five samples from one session cannot establish
normal variation across days.

Profiles compare the same input type, reject known device-ID mismatches, and skip
invalid or unreadable references. Spatial features and speeds use aspect-preserving
unit-box normalization; duration and pauses retain their actual seconds. Pauses
use the existing raw-pixel speed heuristic. Pressure comparisons additionally
require matching reported device IDs and complete, varying pressure signals;
missing or constant pressure is never inferred. Device IDs are driver reports,
not proof of calibration. Legacy records without session metadata still work,
with their session coverage shown as unknown.

### Collection sessions and capture quality

Capture panels display a generated **Session** UUID, an optional session note,
and a self-reported research label: unlabelled, genuine, attempted imitation, or
synthetic/demonstration. The label is collection metadata; it is not inferred by
the application and does not alter the existing verification decision. Each dialog
starts a new session. **Clear** and saving preserve that session; **New session**
is available after clearing the canvas. Keep repeated captures from the same
collection session grouped, and start another session for a later collection.

New captures preserve session IDs, notes, labels, canvas dimensions, and reported
sensor capabilities in the existing JSON metadata. These analysis fields do not
require changes to the raw sample tables. Enrollment also stores versioned descriptive profile snapshots; viewer
profiles are recomputed from raw references to allow exclusion of the selected
sample. No existing records are rewritten.

After pointer release, capture quality appears below the canvas. Gaps greater than
100 ms within a stroke, median sampling below 20 Hz, duplicate timestamps, partial
or constant pressure, and mixed input are flagged for review. Pen-up gaps are
treated as possible pauses rather than lost within-stroke events. Quality warnings
are advisory; existing validity errors still prevent saving. These initial
thresholds are configurable in `CaptureConfig` and need real-device validation.

## Mouse, touchscreen, stylus, and Samsung tablets

- Mouse supplies coordinates, event timing, and stroke boundaries. Pressure and
  tilt remain `None`; no pressure values are fabricated.
- Finger input uses Qt touch events where the Windows device/driver exposes
  them. Only one contact is captured at a time. Coordinates and timing are useful;
  pressure may be missing, constant, or unreliable. Qt pressure is recorded only
  when the device reports that capability, without claiming calibration.
- Compatible stylus/tablet input uses `QTabletEvent`; pressure, x/y tilt, rotation,
  buttons, and system device ID are recorded when exposed. Unsupported sensors
  stay `None`. Qt-synthesized mouse events are suppressed to avoid duplicates.

A Samsung Android tablet is **not automatically a native Windows input device**.
If used through display/input bridge software, the measurements available depend
on what that bridge forwards to Qt; it may expose only mouse-like input. S Pen
pressure and tilt are not guaranteed. No Android-specific transport or bridge is
implemented here. A future companion app can submit the same domain model through
an explicit local import/transport design. The desktop app works without pressure
or tilt and makes no Android hardware assumptions.

## Architecture

```text
main.py                    entry point
stroke_key/app.py          Qt lifecycle, storage initialization
stroke_key/config.py       centralized validity and experimental scoring parameters
stroke_key/gui/            windows, dialogs, live canvas, plots
stroke_key/models/         dataclass point, sample, user models and validation
stroke_key/capture/        device-independent events and monotonic recorder
stroke_key/processing/     normalization, features, DTW, matcher
stroke_key/storage/        SQLite schema and repositories
stroke_key/services/       enrollment and verification workflows
stroke_key/utils/          project-relative paths and development logging
tests/                     synthetic numerical, persistence and Qt integration tests
data/                      local database and development log (ignored by Git)
```

Processing has no GUI or SQL dependency. Repositories own parameterized SQL.
SQLite uses foreign keys and ordered point rows; enrollment is atomic. Qt input
adapters share the recorder. Reference data is loaded on the GUI thread and pure
matching runs separately, so SQLite connections never cross threads.

## Data format

Schema version 2 preserves the original three tables: `users`, `signature_samples`,
and `signature_points`. Each sample has a UUID, optional user UUID, UTC creation time,
input type, duration, stroke count, and JSON metadata. Measurements have an
explicit point index and these columns:

| Field | Meaning |
| --- | --- |
| x, y | Floating-point canvas pixels; origin top left |
| timestamp | UTC epoch seconds derived from monotonic elapsed time |
| elapsed_time | Seconds since first pointer down |
| pressure | Qt-reported value, usually 0–1, or SQL NULL |
| stroke_number | Increasing integer, starting at 1 |
| pointer_state | down, move, up (model also permits cancel) |
| device_type | mouse, touch, stylus, or imported source |
| tilt_x, tilt_y | Qt-reported degrees, or NULL |
| orientation | Qt stylus rotation in degrees, or NULL |
| stylus_buttons | Qt button bitmask, or NULL |
| device_id | Optional device system identifier |

Metadata records canvas dimensions, units, capture interruption status and
research purpose. Time-series points are stored in relational rows, not images.
Raw data remains unchanged during processing. Normalization produces a separate
copy translated to bounding-box origin and uniformly scaled to fit a requested
width/height (default 1×1). Relative time starts at zero; actual durations, absolute
timestamps, sensor values and stroke membership are preserved.

### Phase 1: reproducible research storage

Startup creates schema version 2 for a new database, or upgrades version 1 in one
transaction. Before upgrading an existing database, SQLite's backup API creates
a local sibling file named `strokekey.db-v1-backup-<timestamp>-<id>`. The schema,
backfill and version advance all roll back if migration fails. Existing raw rows,
participants and metadata are preserved exactly. Unknown legacy session metadata
remains unknown. Future unsupported schema versions are rejected without being
downgraded. Backup files contain the same unencrypted biometric data as the database.

The additional tables are:

| Tables | Purpose |
| --- | --- |
| `reference_sets`, `reference_set_samples` | Ordered, versioned enrollment references grouped by participant, input type and reported device IDs |
| `research_trials` | Separate candidate sample, claimed participant, optional reported signer, declared attempt type, session, consent confirmation and matcher/result snapshots |
| `evaluation_runs`, `evaluation_run_trials` | Saved evaluation selections, matcher configuration and results linked to immutable trials |

Migration publishes initial reference sets for existing enrollments without
reinterpreting or repairing the raw data. New enrollment completion publishes
reference sets atomically with participant creation and sample association.
Verification reads the latest published revision of each enrollment device group.
Publishing another revision leaves all older versions intact; merely saving an
assigned sample does not automatically add it to live verification.

Published reference sets and their raw measurements are immutable. Saved trial
records, candidate measurements, evaluation snapshots and selections are also
immutable. SQL triggers and foreign keys prevent ordinary writes or deletes that
would change recorded evidence. This is data-integrity protection, not encryption
or protection against someone who can replace the file or remove SQL triggers.
Unassigned drafts retain their existing retry/delete workflow, while research
trial candidates cannot be retried or promoted into enrollment.

Backend entry points in `storage/research_repositories.py`:

- `ReferenceSetRepository.create`, `get`, `latest_for_user`, and `samples_for` publish and read reference versions.
- `ResearchTrialRepository.save(candidate, trial)` atomically saves a fresh candidate and explicit trial; `get` and `list_trials` read snapshots.
- `EvaluationRunRepository.save`, `get`, and `list_runs` preserve evaluation records.

`models/research.py` defines these records. Trial persistence checks consent,
declaration consistency, capture/session consistency, a complete compatible
reference set, the full matcher configuration, per-reference score coverage,
decisions against the saved threshold, and median aggregation. Saved JSON metrics
must be finite; unavailable evaluation rates should use `null` rather than NaN.
Repository transactions use nested savepoints so a downstream failure rolls back
the entire calling workflow.

Phase 1 supplies persistence and existing-workflow integration. The guided
enrollment interface, trial-collection interface and evaluation dashboard remain
the next three phases. Ordinary verification still does not save candidates.

## Features and current matching algorithm

Features include duration, within-stroke path length, bounding-box dimensions,
aspect ratio, stroke count, mean/max/median/std velocity, mean/max acceleration
magnitude, pause count/duration, mean stroke duration/length, and available
pressure mean/std/min/max. Average velocity is path length divided by measured
within-stroke time. Derivatives ignore zero-time segments and pen-up transitions.
Acceleration is the absolute change in adjacent segment speeds divided by the
later segment interval. A pause is a contiguous pen-up or low-speed interval
(≤5 raw pixels/s) lasting at least 150 ms. These heuristics need device calibration.
Aspect ratio is recorded as 0 for zero-height samples; comparison instead uses
normalized width and height so horizontal/vertical strokes remain well-defined.

The local DTW implementation compares normalized `(x, y)` trajectories using
Euclidean point distance. The final distance is total cost divided by the number
of alignments on the minimum-total-cost path; similarity is `exp(-distance / 0.20)`.
It supports unequal lengths and arbitrary feature dimensions. Comparison limits
each trajectory to 256 evenly indexed original points to bound O(n×m) time;
this is subsampling, not interpolation or uniform time resampling. Raw data is
never reduced. DTW currently concatenates stroke trajectories; it does not enforce
stroke-by-stroke alignment. Stroke count contributes a separate score.

Overall similarity combines DTW (55%), duration ratio (15%), normalized path
length and bounding-box extent ratios (20%), and stroke-count ratio (10%). Ratios
use smaller/larger, with two zero values considered equal. Scores are clamped
to [0, 1]. Verification takes the median across references, with an initial
threshold of **0.75**. These settings are **experimental and uncalibrated**;
pressure and tilt are recorded but do not influence acceptance yet. There is no
claim about FAR, FRR, EER, spoof resistance, identity assurance, or cross-device
performance. A visually similar trajectory may match despite different dynamics.

## Privacy and security limitations

All application data stays in the local project directory. There is no telemetry,
analytics, API client, external upload, network listener, cloud service or sync
feature. Avoid putting this directory in an OS/cloud-synchronized location if
strictly local handling is required. Biometric data is sensitive: obtain informed
consent and collect only necessary research samples. Git ignores database and
log files, but that is not access control.

**SQLite is not encrypted**, and this version adds no encryption. Anyone with
access to the files or machine may read or alter samples. There are no secure
templates, liveness checks, audit controls, session authentication or production
retention/deletion policy. Exception logs at `data/strokekey.log` aid development;
raw points are never logged intentionally. GUI messages handle common input and
storage failures without exposing tracebacks. To reset a disposable dataset,
close the app and remove `data/strokekey.db`; it will be recreated on next launch.
Backups and any SQLite sidecars also contain sensitive data.

Production deployment would require substantially stronger privacy, security,
correct encryption and key management, consent, retention controls, threat
modeling, regulatory review, and rigorous biometric evaluation. This prototype
must not be used to protect accounts or replace passwords.

## Tests and checks

```powershell
python -m pytest -q
python -m compileall -q main.py stroke_key tests
python -m pip check
```

Tests generate synthetic trajectories in temporary databases. They cover
translation/scaling, timing and stroke preservation, feature edge cases, DTW,
score bounds, median aggregation, raw persistence, transactions, malformed
metadata, mouse event capture, enrollment retry, verification and plotting. Qt
tests run offscreen without extra pytest plugins. Real Windows touch/stylus
behavior still requires a hardware/driver test; automated synthetic events do
not establish hardware compatibility.

## Planned improvements

The first analysis milestone provides capture diagnostics, collection-session
metadata, personal profiles, overlays and measured explanations. Phase 1 adds
transactional migration and reproducible reference/trial/evaluation storage.
Phase 2 will add guided enrollment and later-session extensions, followed by
Phase 3 trial collection and Phase 4 evaluation. Research still requires a
**consented, repeatable capture and evaluation protocol**: verify
devices, sampling rates, repeatability and quality across sessions; then measure
genuine/impostor distributions, FAR/FRR and EER before changing thresholds.
Follow with stroke-aware resampling/interpolation, device calibration and richer
timing/pressure/tilt features. Later research may add local Android/S Pen capture,
multi-user datasets, learned sequence classifiers or PyTorch models, encrypted
templates, and carefully designed passkey/FIDO integration. None of these are
implemented or promised by the current matching score.
