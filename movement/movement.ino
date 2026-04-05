/*
 * Navigation hybrid (two layers):
 *
 * **Layer A — local “VFH-lite” / follow-the-gap (cruise)**
 *   While the closest front reading is between OBSTACLE_CM and SOFT_APPROACH_MAX_CM,
 *   drive forward with gentle differential steering: steer toward the side whose angled
 *   front (FL vs FR) sees more range; if FM is the tightest, nudge toward the more open
 *   wing. No extra sonar reads — uses the same throttled FL/FM/FR as straight cruise.
 *   If any front is tooClose → stop and use Layer-Avoid PREP (discrete 45°/90°/reverse).
 *
 * **Layer B — trap escape (planning bias)**
 *   If reverse+extra ends without a trap turn twice within TRAP_WINDOW_MS, assume a local
 *   minimum (U-trap). For LAYER_B_DURATION_MS, force **front-only** turn decisions (same
 *   idea as post-turn) and slightly stronger Layer-A steering so side readings don’t flip
 *   the planner. Cleared when all three fronts are past CLEAR_CM for cruise.
 *
 * Three front HC-SR04: FL / FM / FR (~45° wings). Side L/R for full 90° planning when
 * neither post-turn nor Layer B is active.
 *
 * Multi-sample in PREP/reverse: min of valid echoes. Cruise fronts: fast double-sample.
 *
 * Arduino Giga R1: plenty of CPU for this logic; optional higher Serial baud below.
 * Echo pins: 3.3 V safe on Giga.
 *
 * Stuck / reverse safety:
 * - REV_CLR must not reset phaseStartMs each loop (fixed): elapsed time must grow for timeouts.
 * - REVERSE_CLEAR_MAX_MS forces REV_X if fronts never satisfy open condition (bad echoes).
 * - reverseFrontsOpenEnough: all three clear OR FM + one wing (one dead sensor).
 * - Layer B + trap counter break some PREP <-> reverse loops.
 *
 * Global stuck counter: trap failures, REV_CLR timeout, and cruise stagnation (same minF
 * band too long) increment it. At STUCK_THRESHOLD → PH_STUCK_RECOVERY: long reverse then
 * two 90° pivots same way (≈ U-turn), then cruise with post-turn front-only.
 *
 * Loop / CPU: HC-SR04 uses blocking pulseIn + delay. Heavy paths are readAllFrontsAndSidesFiltered
 * (15 pings) and readFrontsTripleFiltered (9). Cruise throttles fronts; REV_CLR uses front-only
 * scans on REV_CLR_SCAN_INTERVAL_MS. Verbose Serial adds latency — use SERIAL_LOG_VERBOSE 0 for demos.
 *
 * Host / ROS (USB Serial, SERIAL_BAUD): line-based commands from the laptop (newline-terminated):
 *   HOLD   — stop motors; freeze navigation (no phase advance / sonar / stuck logic this loop).
 *   RUN    — resume autonomous navigation from current phase.
 *   STATUS or ? — reply with one line: EXT_HOLD 0|1
 * Replies: OK HOLD, OK RUN, or ERR <line>. For stable control + logging, set SERIAL_LOG_VERBOSE 0.
 */

#include <string.h>

#define L_RPWM 2
#define L_LPWM 3
#define L_REN 4
#define L_LEN 5
#define R_RPWM 6
#define R_LPWM 7
#define R_REN 9
#define R_LEN 10

#define TRIG_FRONT_L 11
#define ECHO_FRONT_L 12
#define TRIG_FRONT_R 22
#define ECHO_FRONT_R 23
#define TRIG_FRONT_MID 24
#define ECHO_FRONT_MID 25
#define TRIG_LEFT 26
#define ECHO_LEFT 27
#define TRIG_RIGHT 28
#define ECHO_RIGHT 29

/** 0–255 at 8-bit PWM (see setup). Raise for faster cruise / turns. */
const uint8_t SPEED_LEFT = 150;
const uint8_t SPEED_RIGHT = 150;

const int OBSTACLE_CM = 45;
const int CLEAR_CM = 52;
/** Layer A: above OBSTACLE_CM up to here → forward + gentle steer (no extra pings). */
const int SOFT_APPROACH_MAX_CM = 78;
const int8_t LAYER_A_MAX_DELTA = 48;

const unsigned long SONAR_INTERVAL_MS = 60;
const unsigned long PREP_SETTLE_MS = 180UL;
const unsigned long BETWEEN_SENSORS_MS = 38UL;
const unsigned long BETWEEN_SAMPLES_MS = 18UL;
/**
 * pulseIn() max wait (µs). 30 ms was conservative; ~23–24 ms still reaches ~4 m round-trip.
 * Lowering slightly reduces worst-case per-ping blocking when echoes are lost (multi-sensor loops).
 */
const unsigned long SONAR_PULSE_TIMEOUT_US = 24000UL;
/** REV_CLR only needs fronts; scan at this interval instead of every loop() (~15 pings → 9). */
const unsigned long REV_CLR_SCAN_INTERVAL_MS = 72UL;

const float WHEEL_DIAMETER_CM = 6.35f;
const float MOTOR_RPM = 200.0f;
const float MOTOR_LOAD_FACTOR = 0.65f;
const float REVERSE_EXTRA_CM = 30.0f;
const float REVERSE_EXTRA_CALIB = 1.12f;

/** Base pivot time at PWM 150; multiply by TURN_CALIB on your floor (Giga: tune here). */
const unsigned long TURN_90_MS_BASE = 650;
/** >1.0 if robot under-rotates; <1.0 if over-rotates. */
const float TURN_CALIB = 1.7f;
/** After a pivot, drive straight this long (no Layer A) so the arc doesn’t re-trigger PREP. */
const unsigned long POST_TURN_STRAIGHT_MS = 450UL;
/** Require this many consecutive “too close” cruise cycles before PREP (drops one-shot noise). */
const uint8_t PREP_TRIGGER_DEBOUNCE = 2;

/** After a turn, ignore L/R side sensors for this long (prevents L/R turn ping-pong). */
const unsigned long POST_TURN_FRONT_ONLY_MS = 2000UL;

/** Layer B: repeated failed trap exit → front-only planning for this long. */
const unsigned long LAYER_B_DURATION_MS = 5000UL;
/** Count trap failures within this window to trigger Layer B. */
const unsigned long TRAP_WINDOW_MS = 12000UL;

unsigned long REVERSE_EXTRA_MS = 700;
/** Max time in REV_CLR; avoids infinite reverse if echoes never pass farEnough (timeouts/bad wiring). */
const unsigned long REVERSE_CLEAR_MAX_MS = 8000UL;

/** Sum of stuck signals (trap, timeout, stagnation) to trigger PH_STUCK_RECOVERY. */
const uint8_t STUCK_THRESHOLD = 4;
/** In cruise Layer-A band: same distance bucket this long → +1 stuck signal (no wheel encoders). */
const unsigned long STUCK_STAGNATION_MS = 4500UL;
/** First segment of stuck recovery: back up longer than normal REVERSE_EXTRA. */
const unsigned long STUCK_REVERSE_MS = 1500UL;

#ifndef SERIAL_BAUD
#define SERIAL_BAUD 115200
#endif

/** 1 = detailed Serial (decisions, phases, sanitize). 0 = minimal (boot + errors only). */
#ifndef SERIAL_LOG_VERBOSE
#define SERIAL_LOG_VERBOSE 1
#endif
/** Cruise status line interval (ms); includes last FL/FM/FR from throttled read. */
#ifndef SERIAL_LOG_TICK_MS
#define SERIAL_LOG_TICK_MS 200UL
#endif

#if SERIAL_LOG_VERBOSE
#define LOG_PREP_LN(msg) \
  do { \
    Serial.print(millis()); \
    Serial.print(F("\t[PREP]\t")); \
    Serial.println(F(msg)); \
  } while (0)
#define LOG_TAG_LN(tag, msg) \
  do { \
    Serial.print(millis()); \
    Serial.print(F("\t[")); \
    Serial.print(F(tag)); \
    Serial.print(F("]\t")); \
    Serial.println(F(msg)); \
  } while (0)
#else
#define LOG_PREP_LN(msg) ((void)0)
#define LOG_TAG_LN(tag, msg) ((void)0)
#endif

/* Must appear before any function: Arduino inserts prototypes before the first function,
 * so logPrintPhase(Phase) would otherwise see an unknown type. */
enum Phase : uint8_t {
  PH_CRUISE,
  PH_PREP,
  PH_AVOID_TL,
  PH_AVOID_TR,
  PH_AVOID_TL45,
  PH_AVOID_TR45,
  PH_REVERSE_CLEAR,
  PH_REVERSE_EXTRA,
  PH_STUCK_RECOVERY
};

Phase phase = PH_CRUISE;
unsigned long phaseStartMs = 0;
unsigned long turnDurationMs = 650;
/** After a completed turn: no Layer A until this time (millis). */
static unsigned long postTurnStraightUntil = 0;

static unsigned long turnMs90() {
  unsigned long t = (unsigned long)((float)TURN_90_MS_BASE * TURN_CALIB);
  if (t < 150UL)
    t = 150UL;
  return t;
}

static unsigned long turnMs45() {
  return (turnMs90() + 1UL) / 2UL;
}

static bool preferRightNext = false;
/** Nonzero: millis() deadline; while active, 90°/45° use only FL/FR/FM, not side ultrasonics. */
static unsigned long postTurnFrontOnlyUntil = 0;

static bool postTurnFrontOnlyActive() {
  return postTurnFrontOnlyUntil != 0 && millis() < postTurnFrontOnlyUntil;
}

static void armPostTurnFrontOnly() {
  postTurnFrontOnlyUntil = millis() + POST_TURN_FRONT_ONLY_MS;
}

static void clearPostTurnFrontOnly() {
  postTurnFrontOnlyUntil = 0;
}

static unsigned long layerBUntil = 0;
static unsigned long trapWindowStartMs = 0;
static uint8_t trapCountInWindow = 0;

/** Trap failures, REV_CLR timeout, cruise stagnation — no encoders; heuristic “not progressing”. */
static uint8_t globalStuckCounter = 0;
/** 0 = long reverse; 1 = first 90°; 2 = second 90° (U-turn). */
static uint8_t stuckRecoveryStep = 0;
static bool stuckRecoveryTurnLeft = true;

/** USB host (laptop): when true, motors stopped and main navigation switch skipped. */
static bool externalHoldActive = false;

static char rosCmdLineBuf[40];
static uint8_t rosCmdLineLen = 0;

static void rosToLowerAscii(char *s) {
  for (; *s; ++s) {
    if (*s >= 'A' && *s <= 'Z')
      *s = (char)(*s - ('A' - 'a'));
  }
}

static void rosApplyLine(char *line) {
  while (*line == ' ' || *line == '\t')
    ++line;
  size_t n = strlen(line);
  while (n > 0 && (line[n - 1] == ' ' || line[n - 1] == '\t')) {
    line[--n] = '\0';
  }
  if (n == 0)
    return;
  rosToLowerAscii(line);
  if (strcmp(line, "hold") == 0) {
    externalHoldActive = true;
    Serial.println(F("OK HOLD"));
    return;
  }
  if (strcmp(line, "run") == 0) {
    externalHoldActive = false;
    Serial.println(F("OK RUN"));
    return;
  }
  if (strcmp(line, "status") == 0 || strcmp(line, "?") == 0) {
    Serial.print(F("EXT_HOLD "));
    Serial.println(externalHoldActive ? '1' : '0');
    return;
  }
  Serial.print(F("ERR "));
  Serial.println(line);
}

/** Non-blocking: accumulate bytes until \\n or \\r, then handle one command line. */
static void pollHostUsbCommands() {
  while (Serial.available() > 0) {
    int ri = Serial.read();
    if (ri < 0)
      break;
    char c = (char)ri;
    if (c == '\n' || c == '\r') {
      if (rosCmdLineLen > 0) {
        rosCmdLineBuf[rosCmdLineLen] = '\0';
        rosApplyLine((char *)rosCmdLineBuf);
        rosCmdLineLen = 0;
      }
    } else if (rosCmdLineLen < sizeof(rosCmdLineBuf) - 1U) {
      rosCmdLineBuf[rosCmdLineLen++] = (uint8_t)c;
    } else {
      rosCmdLineLen = 0;
    }
  }
}

static bool layerBActive() {
  return layerBUntil != 0 && millis() < layerBUntil;
}

static void clearLayerB() {
  layerBUntil = 0;
  trapCountInWindow = 0;
}

/** Call when reverse+extra finishes but no trap turn was possible → PREP again. */
static void recordTrapFailure() {
  unsigned long t = millis();
  if (t - trapWindowStartMs > TRAP_WINDOW_MS) {
    trapWindowStartMs = t;
    trapCountInWindow = 0;
  }
  trapCountInWindow++;
  if (globalStuckCounter < 250)
    globalStuckCounter++;
#if SERIAL_LOG_VERBOSE
  Serial.print(t);
  Serial.print(F("\t[TRAP]\tfail count in window="));
  Serial.print(trapCountInWindow);
  Serial.print(F(" (need 2 for Layer B)"));
  Serial.println();
#endif
  if (trapCountInWindow >= 2) {
    layerBUntil = t + LAYER_B_DURATION_MS;
    trapCountInWindow = 0;
    trapWindowStartMs = t;
    armPostTurnFrontOnly();
#if SERIAL_LOG_VERBOSE
    Serial.print(t);
    Serial.print(F("\t[TRAP]\tLayer B ON for "));
    Serial.print(LAYER_B_DURATION_MS);
    Serial.println(F(" ms + post-turn front-only"));
#endif
  }
}

static bool useFrontOnlyPlanning() {
  return postTurnFrontOnlyActive() || layerBActive();
}

static void logPrintPhase(Phase p) {
  switch (p) {
  case PH_CRUISE:
    Serial.print(F("CRUISE"));
    break;
  case PH_PREP:
    Serial.print(F("PREP"));
    break;
  case PH_AVOID_TL:
    Serial.print(F("TURN90L"));
    break;
  case PH_AVOID_TR:
    Serial.print(F("TURN90R"));
    break;
  case PH_AVOID_TL45:
    Serial.print(F("TURN45L"));
    break;
  case PH_AVOID_TR45:
    Serial.print(F("TURN45R"));
    break;
  case PH_REVERSE_CLEAR:
    Serial.print(F("REV_CLR"));
    break;
  case PH_REVERSE_EXTRA:
    Serial.print(F("REV_X"));
    break;
  case PH_STUCK_RECOVERY:
    Serial.print(F("STUCK_RX"));
    break;
  default:
    Serial.print((int)p);
    break;
  }
}

#if SERIAL_LOG_VERBOSE
static void logPhaseTransition(Phase from, Phase to) {
  Serial.print(millis());
  Serial.print(F("\tPHASE\t"));
  logPrintPhase(from);
  Serial.print(F(" => "));
  logPrintPhase(to);
  Serial.println();
}
#endif

void motorsStop() {
  analogWrite(L_RPWM, 0);
  analogWrite(L_LPWM, 0);
  analogWrite(R_RPWM, 0);
  analogWrite(R_LPWM, 0);
}

void forwardDrive() {
  analogWrite(L_RPWM, 0);
  analogWrite(L_LPWM, SPEED_LEFT);
  analogWrite(R_RPWM, 0);
  analogWrite(R_LPWM, SPEED_RIGHT);
}

/** Layer A: forward with differential steer. delta > 0 → arc right (left wheel faster). */
void forwardDriveSteered(int8_t delta) {
  int l = (int)SPEED_LEFT + (int)delta;
  int r = (int)SPEED_RIGHT - (int)delta;
  if (l < 70)
    l = 70;
  if (r < 70)
    r = 70;
  if (l > 255)
    l = 255;
  if (r > 255)
    r = 255;
  analogWrite(L_RPWM, 0);
  analogWrite(L_LPWM, (uint8_t)l);
  analogWrite(R_RPWM, 0);
  analogWrite(R_LPWM, (uint8_t)r);
}

void backwardDrive() {
  analogWrite(L_RPWM, SPEED_LEFT);
  analogWrite(L_LPWM, 0);
  analogWrite(R_RPWM, SPEED_RIGHT);
  analogWrite(R_LPWM, 0);
}

void turnLeftDrive() {
  analogWrite(L_RPWM, SPEED_LEFT);
  analogWrite(L_LPWM, 0);
  analogWrite(R_RPWM, 0);
  analogWrite(R_LPWM, SPEED_RIGHT);
}

void turnRightDrive() {
  analogWrite(L_RPWM, 0);
  analogWrite(L_LPWM, SPEED_LEFT);
  analogWrite(R_RPWM, SPEED_RIGHT);
  analogWrite(R_LPWM, 0);
}

static void enterReverseClear() {
  LOG_TAG_LN("REV", "enter REV_CLR (cleared post-turn timer)");
  clearPostTurnFrontOnly();
  phase = PH_REVERSE_CLEAR;
  phaseStartMs = millis();
  backwardDrive();
}

static bool phaseIsAvoidTurn(Phase p) {
  return p == PH_AVOID_TL || p == PH_AVOID_TR || p == PH_AVOID_TL45 || p == PH_AVOID_TR45;
}

/** Long back + two 90° same direction ≈ U-turn; resets trap/Layer-B bias via prefer flip. */
static void enterStuckRecovery() {
  LOG_TAG_LN("STUCK", "recovery U-turn (long back + 2x90)");
  motorsStop();
  clearPostTurnFrontOnly();
  clearLayerB();
  trapCountInWindow = 0;
  trapWindowStartMs = millis();
  stuckRecoveryTurnLeft = !preferRightNext;
  stuckRecoveryStep = 0;
  phase = PH_STUCK_RECOVERY;
  phaseStartMs = millis();
  backwardDrive();
}

static int readCmRaw(uint8_t trig, uint8_t echo) {
  digitalWrite(trig, LOW);
  delayMicroseconds(2);
  digitalWrite(trig, HIGH);
  delayMicroseconds(10);
  digitalWrite(trig, LOW);

  unsigned long dur = pulseIn(echo, HIGH, SONAR_PULSE_TIMEOUT_US);
  if (dur == 0)
    return 999;
  long cm = (long)((dur * 0.034f) / 2.0f);
  if (cm < 2)
    return 2;
  if (cm > 400)
    return 400;
  return (int)cm;
}

/** Valid distance returned from readCmRaw (not a missed pulse). */
static bool echoValid(int cm) {
  return cm >= 2 && cm < 998;
}

/**
 * Three pings; use the **minimum valid** distance (closest real object).
 * Missed echoes (999) are ignored so e.g. (3, 999, 999) → 3, not 999.
 * If all three miss, returns 999.
 */
static int readCmMinOfValid3(uint8_t trig, uint8_t echo) {
  int a = readCmRaw(trig, echo);
  delay(BETWEEN_SAMPLES_MS);
  int b = readCmRaw(trig, echo);
  delay(BETWEEN_SAMPLES_MS);
  int c = readCmRaw(trig, echo);
  int best = 999;
  if (echoValid(a) && a < best)
    best = a;
  if (echoValid(b) && b < best)
    best = b;
  if (echoValid(c) && c < best)
    best = c;
  if (best < 999)
    return best;
  return 999;
}

static bool tooClose(int cm) {
  return (cm <= OBSTACLE_CM && cm >= 3);
}

static bool farEnough(int cm) {
  if (cm >= 998)
    return false;
  return cm > CLEAR_CM;
}

static bool sideBlocked(int cm) {
  return tooClose(cm);
}

static bool cornerOkForTurn(int cm) {
  if (cm >= 998)
    return true;
  return !tooClose(cm);
}

/** Side path wide enough to pivot (not the strict “clear” band used for cruise exit). */
static bool sideOkForPivot(int cm) {
  if (cm >= 998)
    return true;
  return !tooClose(cm);
}

static bool corridorLeftOk(int fL, int l) {
  return cornerOkForTurn(fL) && sideOkForPivot(l);
}

static bool corridorRightOk(int fR, int r) {
  return cornerOkForTurn(fR) && sideOkForPivot(r);
}

static bool verify45LeftOk() {
  if (useFrontOnlyPlanning())
    return cornerOkForTurn(readCmMinOfValid3(TRIG_FRONT_L, ECHO_FRONT_L));
  return corridorLeftOk(
      readCmMinOfValid3(TRIG_FRONT_L, ECHO_FRONT_L),
      readCmMinOfValid3(TRIG_LEFT, ECHO_LEFT));
}

static bool verify45RightOk() {
  if (useFrontOnlyPlanning())
    return cornerOkForTurn(readCmMinOfValid3(TRIG_FRONT_R, ECHO_FRONT_R));
  return corridorRightOk(
      readCmMinOfValid3(TRIG_FRONT_R, ECHO_FRONT_R),
      readCmMinOfValid3(TRIG_RIGHT, ECHO_RIGHT));
}

/** FL/FM/FR only (min-of-3 each). Use for REV_CLR — reverseFrontsOpenEnough does not need sides. */
static void readFrontsTripleFiltered(int &fL, int &fM, int &fR) {
  fL = readCmMinOfValid3(TRIG_FRONT_L, ECHO_FRONT_L);
  delay(BETWEEN_SENSORS_MS);
  fM = readCmMinOfValid3(TRIG_FRONT_MID, ECHO_FRONT_MID);
  delay(BETWEEN_SENSORS_MS);
  fR = readCmMinOfValid3(TRIG_FRONT_R, ECHO_FRONT_R);
}

static void readAllFrontsAndSidesFiltered(int &fL, int &fM, int &fR, int &l, int &r) {
  readFrontsTripleFiltered(fL, fM, fR);
  delay(BETWEEN_SENSORS_MS);
  l = readCmMinOfValid3(TRIG_LEFT, ECHO_LEFT);
  delay(BETWEEN_SENSORS_MS);
  r = readCmMinOfValid3(TRIG_RIGHT, ECHO_RIGHT);
}

/** Cruise: two quick pings; use **minimum valid** (closest obstacle). */
static int readCmFast2(uint8_t trig, uint8_t echo) {
  int a = readCmRaw(trig, echo);
  delay(12);
  int b = readCmRaw(trig, echo);
  if (echoValid(a) && echoValid(b))
    return (a < b) ? a : b;
  if (echoValid(a))
    return a;
  if (echoValid(b))
    return b;
  return 999;
}

#if SERIAL_LOG_VERBOSE
static int s_tickFL = 999, s_tickFM = 999, s_tickFR = 999;
#endif

static void frontTripleThrottled(unsigned long now, int &outFL, int &outFM, int &outFR, bool doPrint) {
  static unsigned long lastPingMs = 0;
  static int lastFL = 999, lastFM = 999, lastFR = 999;
  if (now - lastPingMs >= SONAR_INTERVAL_MS) {
    lastPingMs = now;
    lastFL = readCmFast2(TRIG_FRONT_L, ECHO_FRONT_L);
    delay(BETWEEN_SENSORS_MS);
    lastFM = readCmFast2(TRIG_FRONT_MID, ECHO_FRONT_MID);
    delay(BETWEEN_SENSORS_MS);
    lastFR = readCmFast2(TRIG_FRONT_R, ECHO_FRONT_R);
#if SERIAL_LOG_VERBOSE
    s_tickFL = lastFL;
    s_tickFM = lastFM;
    s_tickFR = lastFR;
#endif
    if (doPrint) {
      Serial.print(F("FL "));
      Serial.print(lastFL);
      Serial.print(F(" FM "));
      Serial.print(lastFM);
      Serial.print(F(" FR "));
      Serial.println(lastFR);
    }
  }
  outFL = lastFL;
  outFM = lastFM;
  outFR = lastFR;
}

static bool allThreeFrontBlocked(int fL, int fM, int fR) {
  return tooClose(fL) && tooClose(fM) && tooClose(fR);
}

static bool frontsClearForCruise(int fL, int fM, int fR) {
  return farEnough(fL) && farEnough(fM) && farEnough(fR);
}

/** Leave reverse-clear: all three open, or center + one wing (one dead/noisy sensor won’t trap you). */
static bool reverseFrontsOpenEnough(int fL, int fM, int fR) {
  if (farEnough(fL) && farEnough(fM) && farEnough(fR))
    return true;
  if (farEnough(fM) && farEnough(fL))
    return true;
  if (farEnough(fM) && farEnough(fR))
    return true;
  return false;
}

static int min3i(int a, int b, int c) {
  int m = a;
  if (b < m)
    m = b;
  if (c < m)
    m = c;
  return m;
}

/**
 * Multipath / crosstalk often returns one absurdly long range while the other wing is real.
 * Pull the outlier down so FL vs FR comparisons don’t flip every ping.
 */
static void sanitizeFrontPair(int &fL, int &fR) {
  if (fL >= 998 && fR >= 998)
    return;
  const int oL = fL;
  const int oR = fR;
  if (fL > 130 && fR < 85)
    fL = fR;
  else if (fR > 130 && fL < 85)
    fR = fL;
#if SERIAL_LOG_VERBOSE
  if (oL != fL || oR != fR) {
    Serial.print(millis());
    Serial.print(F("\t[SAN]\tFL "));
    Serial.print(oL);
    Serial.print(F("=>"));
    Serial.print(fL);
    Serial.print(F(" FR "));
    Serial.print(oR);
    Serial.print(F("=>"));
    Serial.println(fR);
  }
#endif
}

/**
 * Layer A steering from FL/FM/FR only (no extra sensor cycle).
 * Positive → steer right (more room on right / FR more open than FL).
 */
static int8_t layerAComputeSteer(int fl, int fm, int fr) {
  sanitizeFrontPair(fl, fr);
  long steer = (long)(fr - fl) * 4L / 16L;
  if (echoValid(fm) && echoValid(fl) && echoValid(fr) && fm <= fl && fm <= fr) {
    if (fr >= fl)
      steer += 14L;
    else
      steer -= 14L;
  }
  if (layerBActive()) {
    steer = steer * 5L / 4L;
  }
  if (steer > (long)LAYER_A_MAX_DELTA)
    steer = LAYER_A_MAX_DELTA;
  if (steer < -(long)LAYER_A_MAX_DELTA)
    steer = -(long)LAYER_A_MAX_DELTA;
  return (int8_t)steer;
}

static bool commit90Left() {
#if SERIAL_LOG_VERBOSE
  Serial.print(millis());
  Serial.print(F("\t[TURN]\t90deg LEFT dur_ms="));
  Serial.println(turnMs90());
#endif
  phase = PH_AVOID_TL;
  turnDurationMs = turnMs90();
  phaseStartMs = millis();
  preferRightNext = true;
  return true;
}

static bool commit90Right() {
#if SERIAL_LOG_VERBOSE
  Serial.print(millis());
  Serial.print(F("\t[TURN]\t90deg RIGHT dur_ms="));
  Serial.println(turnMs90());
#endif
  phase = PH_AVOID_TR;
  turnDurationMs = turnMs90();
  phaseStartMs = millis();
  preferRightNext = false;
  return true;
}

/**
 * 90° after a recent turn: **only angled fronts FL/FR** (no side sensors — they still see the wall).
 * Uses sanitized ranges + tie band so spurious 300+ cm readings don’t alternate L/R every cycle.
 */
static bool plan90TurnSafeFrontOnly(int fL, int fR) {
  sanitizeFrontPair(fL, fR);
  const bool canL = cornerOkForTurn(fL);
  const bool canR = cornerOkForTurn(fR);
  const int diff = (fL > fR) ? (fL - fR) : (fR - fL);

  if (diff <= 12) {
    if (preferRightNext && canR)
      return commit90Right();
    if (!preferRightNext && canL)
      return commit90Left();
    if (canR)
      return commit90Right();
    if (canL)
      return commit90Left();
    return false;
  }
  if (fR > fL) {
    if (canR)
      return commit90Right();
    if (canL)
      return commit90Left();
    return false;
  }
  if (fL > fR) {
    if (canL)
      return commit90Left();
    if (canR)
      return commit90Right();
    return false;
  }
  return false;
}

/**
 * Normal 90°: uses **side** L/R vs angled fronts (more open side first).
 */
static bool plan90TurnSafeFull(int fL, int fR, int l, int r) {
  const bool canL = corridorLeftOk(fL, l);
  const bool canR = corridorRightOk(fR, r);

  if (l > r) {
    if (canL)
      return commit90Left();
    if (canR)
      return commit90Right();
    return false;
  }
  if (r > l) {
    if (canR)
      return commit90Right();
    if (canL)
      return commit90Left();
    return false;
  }
  if (preferRightNext) {
    if (canR)
      return commit90Right();
    if (canL)
      return commit90Left();
  } else {
    if (canL)
      return commit90Left();
    if (canR)
      return commit90Right();
  }
  return false;
}

static bool plan90TurnSafe(int fL, int fR, int l, int r) {
  if (useFrontOnlyPlanning())
    return plan90TurnSafeFrontOnly(fL, fR);
  return plan90TurnSafeFull(fL, fR, l, r);
}

static bool commit45Left() {
  if (!verify45LeftOk()) {
    LOG_PREP_LN("45L verify FAIL (side or FL blocked)");
    return false;
  }
#if SERIAL_LOG_VERBOSE
  Serial.print(millis());
  Serial.print(F("\t[TURN]\t45deg LEFT dur_ms="));
  Serial.println(turnMs45());
#endif
  phase = PH_AVOID_TL45;
  turnDurationMs = turnMs45();
  phaseStartMs = millis();
  preferRightNext = false;
  return true;
}

static bool commit45Right() {
  if (!verify45RightOk()) {
    LOG_PREP_LN("45R verify FAIL (side or FR blocked)");
    return false;
  }
#if SERIAL_LOG_VERBOSE
  Serial.print(millis());
  Serial.print(F("\t[TURN]\t45deg RIGHT dur_ms="));
  Serial.println(turnMs45());
#endif
  phase = PH_AVOID_TR45;
  turnDurationMs = turnMs45();
  phaseStartMs = millis();
  preferRightNext = true;
  return true;
}

/**
 * After PREP scan: classify obstacle zone and commit phase.
 * Returns true if a maneuver was started.
 */
static bool planPrepManeuver(int fL, int fM, int fR, int l, int r) {
  const bool bl = tooClose(fL);
  const bool bm = tooClose(fM);
  const bool br = tooClose(fR);

#if SERIAL_LOG_VERBOSE
  Serial.print(millis());
  Serial.print(F("\t[PREP]\tscan FL="));
  Serial.print(fL);
  Serial.print(F(" FM="));
  Serial.print(fM);
  Serial.print(F(" FR="));
  Serial.print(fR);
  Serial.print(F(" L="));
  Serial.print(l);
  Serial.print(F(" R="));
  Serial.print(r);
  Serial.print(F(" | bl bm br="));
  Serial.print(bl);
  Serial.print(' ');
  Serial.print(bm);
  Serial.print(' ');
  Serial.print(br);
  Serial.print(F(" frontOnlyPlan="));
  Serial.print(useFrontOnlyPlanning() ? 1 : 0);
  Serial.print(F(" prefR="));
  Serial.println(preferRightNext ? 1 : 0);
#endif

  if (allThreeFrontBlocked(fL, fM, fR)) {
    LOG_PREP_LN("zone ALL3 -> reverse");
    enterReverseClear();
    return true;
  }

  if (bm && bl && !br) {
    LOG_PREP_LN("zone FRONT_LEFT (FM+FL) -> try 45R then 90 then rev");
    if (commit45Right())
      return true;
    if (plan90TurnSafe(fL, fR, l, r))
      return true;
    LOG_PREP_LN("fallback reverse");
    enterReverseClear();
    return true;
  }

  if (bm && br && !bl) {
    LOG_PREP_LN("zone FRONT_RIGHT (FM+FR) -> try 45L then 90 then rev");
    if (commit45Left())
      return true;
    if (plan90TurnSafe(fL, fR, l, r))
      return true;
    LOG_PREP_LN("fallback reverse");
    enterReverseClear();
    return true;
  }

  if (!bm && bl && !br) {
    LOG_PREP_LN("zone WING_L only -> try 45R then 90 then rev");
    if (commit45Right())
      return true;
    if (plan90TurnSafe(fL, fR, l, r))
      return true;
    LOG_PREP_LN("fallback reverse");
    enterReverseClear();
    return true;
  }

  if (!bm && !bl && br) {
    LOG_PREP_LN("zone WING_R only -> try 45L then 90 then rev");
    if (commit45Left())
      return true;
    if (plan90TurnSafe(fL, fR, l, r))
      return true;
    LOG_PREP_LN("fallback reverse");
    enterReverseClear();
    return true;
  }

  if (bl && br && !bm) {
    LOG_PREP_LN("zone BOTH_WINGS (clear FM) -> 90 or rev");
    if (plan90TurnSafe(fL, fR, l, r))
      return true;
    LOG_PREP_LN("fallback reverse");
    enterReverseClear();
    return true;
  }

  if (bm) {
    LOG_PREP_LN("zone CENTER_FM -> 90 or rev");
    if (plan90TurnSafe(fL, fR, l, r))
      return true;
    LOG_PREP_LN("fallback reverse");
    enterReverseClear();
    return true;
  }

  if (!bl && !bm && !br) {
    /* No “resume cruise” branch here: PREP only calls this when !frontsClearForCruise (same scan). */
    LOG_PREP_LN("hysteresis -> try 90 then rev");
    if (plan90TurnSafe(fL, fR, l, r))
      return true;
    LOG_PREP_LN("fallback reverse");
    enterReverseClear();
    return true;
  }

  LOG_PREP_LN("zone FALLTHROUGH -> 90 then rev");
  if (plan90TurnSafe(fL, fR, l, r))
    return true;
  LOG_PREP_LN("fallback reverse");
  enterReverseClear();
  return true;
}

static bool planTrapTurnSafe(int fL, int fM, int fR, int l, int r) {
  if (allThreeFrontBlocked(fL, fM, fR)) {
    LOG_TAG_LN("TRAP", "exit reverse: all3 still blocked -> no turn, go PREP");
    return false;
  }
  if (useFrontOnlyPlanning()) {
    if (plan90TurnSafeFrontOnly(fL, fR))
      return true;
    LOG_TAG_LN("TRAP", "front-only 90 plan failed");
    return false;
  }
  if (plan90TurnSafeFull(fL, fR, l, r))
    return true;
  LOG_TAG_LN("TRAP", "full 90 plan failed");
  return false;
}

static void computeManeuverTimings() {
  const float reverseRpm = MOTOR_LOAD_FACTOR * MOTOR_RPM;
  const float reverseVelCmS = 3.14159265f * WHEEL_DIAMETER_CM * (reverseRpm / 60.0f);
  float extraBackTimeSec = (REVERSE_EXTRA_CM / reverseVelCmS) * REVERSE_EXTRA_CALIB;
  if (extraBackTimeSec < 0.1f)
    extraBackTimeSec = 0.1f;
  REVERSE_EXTRA_MS = (unsigned long)(extraBackTimeSec * 1000.0f);
}

void setup() {
#if defined(ARDUINO_ARCH_MBED)
  /* Must stay 8-bit: all motor values (e.g. 150) assume 0–255 ≈ classic Uno duty.
     analogWriteResolution(10) makes 150 mean 150/1023 (~15%) → robot crawls. */
  analogWriteResolution(8);
#endif
  pinMode(L_RPWM, OUTPUT);
  pinMode(L_LPWM, OUTPUT);
  pinMode(L_REN, OUTPUT);
  pinMode(L_LEN, OUTPUT);
  pinMode(R_RPWM, OUTPUT);
  pinMode(R_LPWM, OUTPUT);
  pinMode(R_REN, OUTPUT);
  pinMode(R_LEN, OUTPUT);

  pinMode(TRIG_FRONT_L, OUTPUT);
  pinMode(ECHO_FRONT_L, INPUT);
  pinMode(TRIG_FRONT_R, OUTPUT);
  pinMode(ECHO_FRONT_R, INPUT);
  pinMode(TRIG_FRONT_MID, OUTPUT);
  pinMode(ECHO_FRONT_MID, INPUT);
  pinMode(TRIG_LEFT, OUTPUT);
  pinMode(ECHO_LEFT, INPUT);
  pinMode(TRIG_RIGHT, OUTPUT);
  pinMode(ECHO_RIGHT, INPUT);

  digitalWrite(L_REN, HIGH);
  digitalWrite(L_LEN, HIGH);
  digitalWrite(R_REN, HIGH);
  digitalWrite(R_LEN, HIGH);

  motorsStop();
  Serial.begin(SERIAL_BAUD);
  unsigned long t0 = millis();
  while (!Serial && (millis() - t0 < 2000UL)) {
  }
  delay(50);
  computeManeuverTimings();
  Serial.println(F("=== robot movement.ino (3-front) Giga ==="));
  Serial.println(F("Host USB: lines HOLD | RUN | STATUS | ? (newline)"));
  Serial.print(F("TURN_90 eff "));
  Serial.print(turnMs90());
  Serial.print(F(" TURN_45 eff "));
  Serial.print(turnMs45());
  Serial.print(F(" TURN_CALIB "));
  Serial.print(TURN_CALIB, 2);
  Serial.print(F(" REVERSE_EXTRA_MS "));
  Serial.print(REVERSE_EXTRA_MS);
  Serial.print(F(" REVERSE_CLR_MAX_MS "));
  Serial.print(REVERSE_CLEAR_MAX_MS);
  Serial.print(F(" POST_TURN_FRONT_ONLY_MS "));
  Serial.print(POST_TURN_FRONT_ONLY_MS);
  Serial.print(F(" LAYER_B_MS "));
  Serial.print(LAYER_B_DURATION_MS);
  Serial.print(F(" SOFT_A "));
  Serial.print(SOFT_APPROACH_MAX_CM);
  Serial.print(F(" SERIAL "));
  Serial.print(SERIAL_BAUD);
  Serial.print(F(" LOG_VERBOSE "));
  Serial.print(SERIAL_LOG_VERBOSE);
  Serial.print(F(" TICK_MS "));
  Serial.println(SERIAL_LOG_TICK_MS);
  Serial.print(F(" STUCK_THR "));
  Serial.print(STUCK_THRESHOLD);
  Serial.print(F(" STAG_MS "));
  Serial.print(STUCK_STAGNATION_MS);
  Serial.print(F(" STUCK_REV_MS "));
  Serial.print(STUCK_REVERSE_MS);
  Serial.print(F(" SONAR_TO_US "));
  Serial.print(SONAR_PULSE_TIMEOUT_US);
  Serial.print(F(" REV_CLR_SCAN_MS "));
  Serial.println(REV_CLR_SCAN_INTERVAL_MS);
}

void loop() {
  const unsigned long now = millis();

  pollHostUsbCommands();

  digitalWrite(L_REN, HIGH);
  digitalWrite(L_LEN, HIGH);
  digitalWrite(R_REN, HIGH);
  digitalWrite(R_LEN, HIGH);

  if (externalHoldActive) {
    motorsStop();
    return;
  }

  if (globalStuckCounter >= STUCK_THRESHOLD && phase != PH_STUCK_RECOVERY) {
    if (!phaseIsAvoidTurn(phase))
      enterStuckRecovery();
  }

#if SERIAL_LOG_VERBOSE
  static unsigned long lastTickMs = 0;
  if (now - lastTickMs >= SERIAL_LOG_TICK_MS) {
    lastTickMs = now;
    Serial.print(now);
    Serial.print(F("\tTICK\t"));
    logPrintPhase(phase);
    Serial.print(F(" PTFO="));
    Serial.print(postTurnFrontOnlyActive() ? 1 : 0);
    Serial.print(F(" LAYB="));
    Serial.print(layerBActive() ? 1 : 0);
    Serial.print(F(" STR8Until="));
    Serial.print((now < postTurnStraightUntil) ? (long)(postTurnStraightUntil - now) : 0L);
    Serial.print(F(" STK="));
    Serial.print(globalStuckCounter);
    if (phase == PH_CRUISE) {
      Serial.print(F(" FL="));
      Serial.print(s_tickFL);
      Serial.print(F(" FM="));
      Serial.print(s_tickFM);
      Serial.print(F(" FR="));
      Serial.print(s_tickFR);
    }
    Serial.println();
  }
#endif

  switch (phase) {
  case PH_CRUISE: {
    int fl = 999, fm = 999, fr = 999;
    frontTripleThrottled(now, fl, fm, fr, !SERIAL_LOG_VERBOSE);
    static uint8_t closeStreak = 0;
    const bool blocked = tooClose(fl) || tooClose(fm) || tooClose(fr);
    static unsigned long cruiseStagnationSince = 0;
    static int cruiseStagnationBucket = -9999;
    if (blocked) {
      cruiseStagnationSince = 0;
      cruiseStagnationBucket = -9999;
      motorsStop();
      if (++closeStreak >= PREP_TRIGGER_DEBOUNCE) {
#if SERIAL_LOG_VERBOSE
        Serial.print(now);
        Serial.print(F("\t[CRUISE]\tblocked -> PREP (debounce ok) minF="));
        Serial.println(min3i(fl, fm, fr));
#endif
        closeStreak = 0;
        phase = PH_PREP;
        phaseStartMs = now;
      }
      break;
    }
    closeStreak = 0;

    if (now < postTurnStraightUntil) {
#if SERIAL_LOG_VERBOSE
      static unsigned long lastStrLog = 0;
      if (now - lastStrLog > 250UL) {
        lastStrLog = now;
        Serial.print(now);
        Serial.print(F("\t[CRUISE]\tpost-turn straight only "));
        Serial.print((long)(postTurnStraightUntil - now));
        Serial.println(F(" ms left"));
      }
#endif
      forwardDrive();
      break;
    }
    const int minF = min3i(fl, fm, fr);
    if (echoValid(minF) && minF > OBSTACLE_CM && minF <= SOFT_APPROACH_MAX_CM) {
      const int b = minF / 5;
      if (b == cruiseStagnationBucket) {
        if (cruiseStagnationSince == 0)
          cruiseStagnationSince = now;
        else if (now - cruiseStagnationSince >= STUCK_STAGNATION_MS) {
          if (globalStuckCounter < 250)
            globalStuckCounter++;
          LOG_TAG_LN("STUCK", "sig: cruise stagnation (Layer-A band)");
          cruiseStagnationSince = now;
        }
      } else {
        cruiseStagnationBucket = b;
        cruiseStagnationSince = now;
      }
      const int8_t steer = layerAComputeSteer(fl, fm, fr);
#if SERIAL_LOG_VERBOSE
      static unsigned long lastLayerLog = 0;
      if (now - lastLayerLog > 400UL) {
        lastLayerLog = now;
        Serial.print(now);
        Serial.print(F("\t[LAYER_A]\tsoft minF="));
        Serial.print(minF);
        Serial.print(F(" steer="));
        Serial.println(steer);
      }
#endif
      forwardDriveSteered(steer);
    } else {
      cruiseStagnationSince = 0;
      cruiseStagnationBucket = -9999;
      if (echoValid(minF) && minF > CLEAR_CM + 5)
        globalStuckCounter = 0;
      forwardDrive();
    }
    break;
  }

  case PH_PREP: {
    motorsStop();
    if (now - phaseStartMs < PREP_SETTLE_MS)
      break;

    int fL, fM, fR, l, r;
    readAllFrontsAndSidesFiltered(fL, fM, fR, l, r);
#if !SERIAL_LOG_VERBOSE
    static unsigned long lastPrepSerialMs = 0;
    if (now - lastPrepSerialMs >= 400UL) {
      lastPrepSerialMs = now;
      Serial.print(F("prep FL "));
      Serial.print(fL);
      Serial.print(F(" FM "));
      Serial.print(fM);
      Serial.print(F(" FR "));
      Serial.print(fR);
      Serial.print(F(" L "));
      Serial.print(l);
      Serial.print(F(" R "));
      Serial.println(r);
    }
#endif

    if (frontsClearForCruise(fL, fM, fR)) {
      clearPostTurnFrontOnly();
      clearLayerB();
      globalStuckCounter = 0;
      phase = PH_CRUISE;
      phaseStartMs = now;
      break;
    }

    planPrepManeuver(fL, fM, fR, l, r);
    break;
  }

  case PH_AVOID_TL:
  case PH_AVOID_TR:
  case PH_AVOID_TL45:
  case PH_AVOID_TR45: {
    bool left = (phase == PH_AVOID_TL || phase == PH_AVOID_TL45);
    if (left)
      turnLeftDrive();
    else
      turnRightDrive();
    if (now - phaseStartMs >= turnDurationMs) {
      motorsStop();
#if SERIAL_LOG_VERBOSE
      Serial.print(now);
      Serial.print(F("\t[TURN]\tdone -> cruise postTurn "));
      Serial.print(POST_TURN_FRONT_ONLY_MS);
      Serial.print(F("ms straight "));
      Serial.print(POST_TURN_STRAIGHT_MS);
      Serial.println(F("ms"));
#endif
      globalStuckCounter = 0;
      armPostTurnFrontOnly();
      postTurnStraightUntil = millis() + POST_TURN_STRAIGHT_MS;
      phase = PH_CRUISE;
      phaseStartMs = now;
    }
    break;
  }

  case PH_REVERSE_CLEAR: {
    if (now - phaseStartMs < 80UL) {
      backwardDrive();
      break;
    }
    const unsigned long revClrElapsed = now - phaseStartMs;
    if (revClrElapsed >= REVERSE_CLEAR_MAX_MS) {
      motorsStop();
      if (globalStuckCounter < 250)
        globalStuckCounter++;
      LOG_TAG_LN("REV", "REV_CLR timeout -> REV_X (proceed anyway)");
      phase = PH_REVERSE_EXTRA;
      phaseStartMs = now;
      break;
    }
    static unsigned long lastRevClrScanMs = 0;
    static unsigned long revClrSessionStartMs = 0;
    static int revClrFL = 999, revClrFM = 999, revClrFR = 999;
    if (phaseStartMs != revClrSessionStartMs) {
      revClrSessionStartMs = phaseStartMs;
      lastRevClrScanMs = 0;
    }
    if (lastRevClrScanMs == 0UL || now - lastRevClrScanMs >= REV_CLR_SCAN_INTERVAL_MS) {
      lastRevClrScanMs = now;
      readFrontsTripleFiltered(revClrFL, revClrFM, revClrFR);
    }
    if (reverseFrontsOpenEnough(revClrFL, revClrFM, revClrFR)) {
      motorsStop();
#if SERIAL_LOG_VERBOSE
      Serial.print(now);
      Serial.print(F("\t[REV]\tpath open -> REV_X extra "));
      Serial.print(REVERSE_EXTRA_MS);
      Serial.print(F("ms elapsedClr="));
      Serial.println(revClrElapsed);
#endif
      phase = PH_REVERSE_EXTRA;
      phaseStartMs = now;
    } else {
      backwardDrive();
      /* Do NOT reset phaseStartMs here — was a bug: elapsed time never grew → reverse forever. */
    }
    break;
  }

  case PH_REVERSE_EXTRA:
    if (now - phaseStartMs < REVERSE_EXTRA_MS) {
      backwardDrive();
    } else {
      motorsStop();
      int fL, fM, fR, l, r;
      readAllFrontsAndSidesFiltered(fL, fM, fR, l, r);
      if (!planTrapTurnSafe(fL, fM, fR, l, r)) {
        recordTrapFailure();
        phase = PH_PREP;
        phaseStartMs = now;
      }
    }
    break;

  case PH_STUCK_RECOVERY: {
    if (stuckRecoveryStep == 0) {
      backwardDrive();
      if (now - phaseStartMs >= STUCK_REVERSE_MS) {
        motorsStop();
        stuckRecoveryStep = 1;
        turnDurationMs = turnMs90();
        phaseStartMs = now;
        if (stuckRecoveryTurnLeft)
          turnLeftDrive();
        else
          turnRightDrive();
      }
      break;
    }
    if (stuckRecoveryStep == 1) {
      if (stuckRecoveryTurnLeft)
        turnLeftDrive();
      else
        turnRightDrive();
      if (now - phaseStartMs >= turnDurationMs) {
        /* No stop between pivots — one continuous ~180° for a clean U-turn. */
        stuckRecoveryStep = 2;
        phaseStartMs = now;
        if (stuckRecoveryTurnLeft)
          turnLeftDrive();
        else
          turnRightDrive();
      }
      break;
    }
    if (stuckRecoveryTurnLeft)
      turnLeftDrive();
    else
      turnRightDrive();
    if (now - phaseStartMs >= turnDurationMs) {
      motorsStop();
      globalStuckCounter = 0;
      preferRightNext = !preferRightNext;
      armPostTurnFrontOnly();
      postTurnStraightUntil = millis() + POST_TURN_STRAIGHT_MS;
      phase = PH_CRUISE;
      phaseStartMs = now;
#if SERIAL_LOG_VERBOSE
      Serial.print(now);
      Serial.print(F("\t[STUCK]\trecovery done -> cruise (180 pivot), prefR="));
      Serial.println(preferRightNext ? 1 : 0);
#endif
    }
    break;
  }

  default:
    phase = PH_CRUISE;
    break;
  }

#if SERIAL_LOG_VERBOSE
  static Phase logPhasePrev = PH_CRUISE;
  if (phase != logPhasePrev) {
    logPhaseTransition(logPhasePrev, phase);
    logPhasePrev = phase;
  }
#endif
}
