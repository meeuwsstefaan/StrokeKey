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

Schema version 1 uses three tables: `users`, `signature_samples`, and
`signature_points`. Each sample has a UUID, optional user UUID, UTC creation time,
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

The recommended next step is a **consented, repeatable capture and evaluation
protocol**: verify devices, sampling rates, repeatability and quality; then measure
genuine/impostor distributions, FAR/FRR and EER before changing thresholds.
Follow with stroke-aware resampling/interpolation, device calibration and richer
timing/pressure/tilt features. Later research may add local Android/S Pen capture,
multi-user datasets, learned sequence classifiers or PyTorch models, encrypted
templates, and carefully designed passkey/FIDO integration. None of these are
implemented or promised by the current matching score.
