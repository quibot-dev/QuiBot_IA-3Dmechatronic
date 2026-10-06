#include <Servo.h>

// Controlador de moviment de Quibot.
//
// Ordres rebudes des de la Raspberry Pi pel port sèrie:
//   GESTURE:GREET_FRIENDLY
//   GESTURE:WAVE
//   JOINT:HAND:OPEN
//   JOINT:SHOULDER:RAISE
//   MOVE:FORWARD:1
//   MOVE:LEFT:2
//   STOP
//
// Els angles, les velocitats i els límits mecànics es defineixen en aquest
// programa per mantenir el control dels actuadors separat de la conversa.

const unsigned long SERIAL_BAUDRATE = 115200;

const int SERVO_COUNT = 3;
const int STEPPER_COUNT = 3;

const uint8_t HAND_SERVO = 0;
const uint8_t WRIST_SERVO = 1;
const uint8_t ELBOW_SERVO = 2;

const int LEFT_WHEEL_STEPPER = 0;
const int RIGHT_WHEEL_STEPPER = 1;
const int SHOULDER_STEPPER = 2;

// Assignació de pins utilitzada al prototip.
const uint8_t SERVO_PINS[SERVO_COUNT] = {4, 5, 6};
const uint8_t STEPPER_STEP_PINS[STEPPER_COUNT] = {8, 10, 12};
const uint8_t STEPPER_DIR_PINS[STEPPER_COUNT] = {9, 11, 13};

const bool STEPPER_DIR_INVERTED[STEPPER_COUNT] = {
  false,  // roda esquerra
  true,   // roda dreta
  false   // espatlla
};

// Posicions i límits mecànics verificats al prototip.
const int HAND_HOME_DEGREES = 50;
const int HAND_MIN_DEGREES = 0;
const int HAND_MAX_DEGREES = 55;

const int WRIST_HOME_DEGREES = 90;
const int WRIST_MIN_DEGREES = 0;
const int WRIST_MAX_DEGREES = 180;

const int ELBOW_HOME_DEGREES = 90;
const int ELBOW_MIN_DEGREES = 65;
const int ELBOW_MAX_DEGREES = 110;

// Motors de 200 passos per volta amb micropassos 1/16:
// 3200 / 360 = 8,8889 passos per grau.
const float STEPS_PER_DEGREE[STEPPER_COUNT] = {8.8889, 8.8889, 8.8889};

// Les rodes treballen amb moviments relatius. La posició de l'espatlla es
// calcula internament i cada moviment aplica la diferència respecte de la
// posició anterior. Com que no hi ha encoder ni final de cursa, cal engegar
// el robot amb l'espatlla situada físicament a SHOULDER_HOME_DEGREES.
const float SHOULDER_HOME_DEGREES = 0.0;
const float SHOULDER_MIN_DEGREES = 0.0;
const float SHOULDER_RAISED_DEGREES = 300.0;
const float SHOULDER_MAX_DEGREES = 600.0;

const unsigned int STEP_PULSE_US = 700;
const unsigned int STEP_PAUSE_US = 700;
const unsigned int SERVO_SETTLE_MS = 250;
const int SERVO_STEP_DEGREES = 1;
const unsigned int SERVO_STEP_DELAY_MS = 12;
const uint8_t EXPLAIN_ARM_REPEATS = 3;
const unsigned int EXPLAIN_ARM_PAUSE_MS = 120;

Servo servos[SERVO_COUNT];
volatile bool stopRequested = false;
int handCurrentDegrees = HAND_HOME_DEGREES;
int wristCurrentDegrees = WRIST_HOME_DEGREES;
int elbowCurrentDegrees = ELBOW_HOME_DEGREES;
float shoulderCurrentDegrees = SHOULDER_HOME_DEGREES;
// Una interrupció del moviment obliga a restablir manualment la referència.
bool shoulderPositionKnown = true;
String pendingCommand = "";
bool hasPendingCommand = false;

struct ArmPose {
  int handDegrees;
  int wristDegrees;
  int elbowDegrees;
  float shoulderDegrees;
  unsigned int holdMs;
  bool returnHome;
};

void handleCommand(const String &line);
void runGesture(const String &gesture);
void runJoint(const String &line);
void runMove(const String &line);
bool parseMoveUnits(const String &value, int &units);
void runWave();
void runExplain();
void applyPose(const ArmPose &pose);
void setArmServos(int handDegrees, int wristDegrees, int elbowDegrees);
void moveArmServosCoordinated(int handTarget, int wristTarget, int elbowTarget);
int interpolateServoDegrees(int startDegrees, int deltaDegrees, int progress, int maxDelta);
void goHome();
void stopForUnknownShoulderPosition();
void moveWheelSteppersByDegrees(float leftDegrees, float rightDegrees);
void moveShoulderToDegrees(float targetDegrees);
void moveSteppersRelative(float deg0, float deg1, float deg2);
void waitWithStop(unsigned int durationMs);
void pollStopCommand();
float clampFloat(float value, float minValue, float maxValue);

// Cada postura defineix els angles de la pinça, el canell, el colze i
// l'espatlla, el temps de manteniment i el retorn opcional al repòs.
// Les rodes només es mouen mitjançant les ordres MOVE:*.
const ArmPose POSE_GREET_FRIENDLY = {
  0,
  0,
  65,
  150.0,
  350,
  true,
};

const ArmPose POSE_EMOTION = {
  15,
  180,
  110,
  300.0,
  450,
  true,
};

const ArmPose POSE_THINKING = {
  35,
  90,
  95,
  0.0,
  700,
  false,
};

const ArmPose POSE_EXPLAIN = {
  20,
  110,
  65,
  50.0,
  400,
  true,
};

const ArmPose POSE_YES = {
  15,
  90,
  100,
  8.0,
  250,
  true,
};

const ArmPose POSE_NO = {
  15,
  90,
  90,
  0.0,
  250,
  true,
};

const ArmPose POSE_EXACTLY = {
  25,
  180,
  110,
  225.0,
  600,
  true,
};

const ArmPose POSE_SAD = {
  50,
  90,
  75,
  0.0,
  800,
  false,
};

const ArmPose POSE_SLEEP = {
  45,
  90,
  65,
  0.0,
  300,
  false,
};

void setup() {
  Serial.begin(SERIAL_BAUDRATE);
  Serial.setTimeout(50);

  for (int i = 0; i < SERVO_COUNT; i++) {
    servos[i].attach(SERVO_PINS[i]);
  }
  setArmServos(HAND_HOME_DEGREES, WRIST_HOME_DEGREES, ELBOW_HOME_DEGREES);

  for (int i = 0; i < STEPPER_COUNT; i++) {
    pinMode(STEPPER_STEP_PINS[i], OUTPUT);
    pinMode(STEPPER_DIR_PINS[i], OUTPUT);
    digitalWrite(STEPPER_STEP_PINS[i], LOW);
    digitalWrite(STEPPER_DIR_PINS[i], LOW);
  }

  Serial.println("READY");
}

void loop() {
  if (hasPendingCommand) {
    String line = pendingCommand;
    pendingCommand = "";
    hasPendingCommand = false;
    handleCommand(line);
    return;
  }

  if (!Serial.available()) {
    return;
  }

  String line = Serial.readStringUntil('\n');
  line.trim();
  line.toUpperCase();

  if (line.length() == 0) {
    return;
  }

  handleCommand(line);
}

void handleCommand(const String &line) {
  stopRequested = false;

  if (line == "STOP") {
    stopRequested = true;
    Serial.println("OK:STOP");
    return;
  }

  if (line.startsWith("GESTURE:")) {
    String gesture = line.substring(8);
    runGesture(gesture);
    return;
  }

  if (line.startsWith("JOINT:")) {
    runJoint(line);
    return;
  }

  if (line.startsWith("MOVE:")) {
    runMove(line);
    return;
  }

  Serial.print("ERR:UNKNOWN_COMMAND:");
  Serial.println(line);
}

void runGesture(const String &gesture) {
  if (gesture == "GREET_FRIENDLY") {
    applyPose(POSE_GREET_FRIENDLY);
  } else if (gesture == "GREET_FORMAL" || gesture == "EMOTION") {
    applyPose(POSE_EMOTION);
  } else if (gesture == "WAVE") {
    runWave();
  } else if (gesture == "THINKING") {
    applyPose(POSE_THINKING);
  } else if (gesture == "EXPLAIN") {
    runExplain();
  } else if (gesture == "YES") {
    applyPose(POSE_YES);
  } else if (gesture == "NO") {
    applyPose(POSE_NO);
  } else if (gesture == "CELEBRATE" || gesture == "EXACTLY") {
    applyPose(POSE_EXACTLY);
  } else if (gesture == "SAD") {
    applyPose(POSE_SAD);
  } else if (gesture == "SLEEP") {
    applyPose(POSE_SLEEP);
  } else {
    Serial.print("ERR:UNKNOWN_GESTURE:");
    Serial.println(gesture);
    return;
  }

  if (stopRequested) {
    return;
  }

  Serial.print("OK:GESTURE:");
  Serial.println(gesture);
}

void runJoint(const String &line) {
  if (line == "JOINT:HAND:OPEN") {
    setArmServos(HAND_MIN_DEGREES, wristCurrentDegrees, elbowCurrentDegrees);
  } else if (line == "JOINT:HAND:CLOSE") {
    setArmServos(HAND_MAX_DEGREES, wristCurrentDegrees, elbowCurrentDegrees);
  } else if (line == "JOINT:ELBOW:EXTEND") {
    setArmServos(handCurrentDegrees, wristCurrentDegrees, ELBOW_MIN_DEGREES);
  } else if (line == "JOINT:ELBOW:FLEX") {
    setArmServos(handCurrentDegrees, wristCurrentDegrees, ELBOW_MAX_DEGREES);
  } else if (line == "JOINT:WRIST:LEFT") {
    setArmServos(handCurrentDegrees, WRIST_MIN_DEGREES, elbowCurrentDegrees);
  } else if (line == "JOINT:WRIST:CENTER") {
    setArmServos(handCurrentDegrees, WRIST_HOME_DEGREES, elbowCurrentDegrees);
  } else if (line == "JOINT:WRIST:RIGHT") {
    setArmServos(handCurrentDegrees, WRIST_MAX_DEGREES, elbowCurrentDegrees);
  } else if (line == "JOINT:SHOULDER:LOWER") {
    moveShoulderToDegrees(SHOULDER_MIN_DEGREES);
  } else if (line == "JOINT:SHOULDER:RAISE") {
    moveShoulderToDegrees(SHOULDER_RAISED_DEGREES);
  } else {
    Serial.println("ERR:UNKNOWN_JOINT_COMMAND:" + line);
    return;
  }

  if (stopRequested) {
    return;
  }

  Serial.println("OK:" + line);
}

void runMove(const String &line) {
  int firstColon = line.indexOf(':');
  int secondColon = line.indexOf(':', firstColon + 1);

  if (secondColon < 0) {
    Serial.print("ERR:BAD_MOVE:");
    Serial.println(line);
    return;
  }

  String direction = line.substring(firstColon + 1, secondColon);
  int units = 0;
  if (!parseMoveUnits(line.substring(secondColon + 1), units)) {
    Serial.print("ERR:BAD_MOVE_UNITS:");
    Serial.println(line);
    return;
  }

  // Increment angular dels motors per cada unitat de moviment.
  if (direction == "FORWARD") {
    moveWheelSteppersByDegrees(30.0 * units, 30.0 * units);
  } else if (direction == "BACKWARD") {
    moveWheelSteppersByDegrees(-30.0 * units, -30.0 * units);
  } else if (direction == "LEFT") {
    moveWheelSteppersByDegrees(-20.0 * units, 20.0 * units);
  } else if (direction == "RIGHT") {
    moveWheelSteppersByDegrees(20.0 * units, -20.0 * units);
  } else {
    Serial.print("ERR:UNKNOWN_DIRECTION:");
    Serial.println(direction);
    return;
  }

  if (stopRequested) {
    return;
  }

  Serial.print("OK:MOVE:");
  Serial.print(direction);
  Serial.print(":");
  Serial.println(units);
}

bool parseMoveUnits(const String &value, int &units) {
  if (value.length() == 0) {
    return false;
  }

  for (unsigned int i = 0; i < value.length(); i++) {
    char c = value.charAt(i);
    if (c < '0' || c > '9') {
      return false;
    }
  }

  units = constrain(value.toInt(), 1, 20);
  return true;
}

void runWave() {
  ArmPose waveA = {
    20,
    110,
    110,
    22.0,
    160,
    false,
  };
  ArmPose waveB = {
    20,
    70,
    105,
    18.0,
    160,
    false,
  };

  applyPose(waveA);
  if (stopRequested) return;
  applyPose(waveB);
  if (stopRequested) return;
  applyPose(waveA);
  if (stopRequested) return;
  goHome();
}

void runExplain() {
  for (uint8_t repeat = 0; repeat < EXPLAIN_ARM_REPEATS; repeat++) {
    setArmServos(POSE_EXPLAIN.handDegrees, POSE_EXPLAIN.wristDegrees, POSE_EXPLAIN.elbowDegrees);
    if (stopRequested) return;
    waitWithStop(POSE_EXPLAIN.holdMs);
    if (stopRequested) return;
    setArmServos(HAND_HOME_DEGREES, WRIST_HOME_DEGREES, ELBOW_HOME_DEGREES);
    if (stopRequested) return;
    waitWithStop(EXPLAIN_ARM_PAUSE_MS);
    if (stopRequested) return;
  }
}

void applyPose(const ArmPose &pose) {
  if (!shoulderPositionKnown) {
    stopForUnknownShoulderPosition();
    return;
  }

  moveShoulderToDegrees(pose.shoulderDegrees);
  if (stopRequested) {
    return;
  }
  setArmServos(pose.handDegrees, pose.wristDegrees, pose.elbowDegrees);

  if (!stopRequested) {
    waitWithStop(pose.holdMs);
  }

  if (pose.returnHome && !stopRequested) {
    goHome();
  }
}

void setArmServos(int handDegrees, int wristDegrees, int elbowDegrees) {
  int safeHand = constrain(handDegrees, HAND_MIN_DEGREES, HAND_MAX_DEGREES);
  int safeWrist = constrain(wristDegrees, WRIST_MIN_DEGREES, WRIST_MAX_DEGREES);
  int safeElbow = constrain(elbowDegrees, ELBOW_MIN_DEGREES, ELBOW_MAX_DEGREES);

  moveArmServosCoordinated(safeHand, safeWrist, safeElbow);
  waitWithStop(SERVO_SETTLE_MS);
}

void moveArmServosCoordinated(int handTarget, int wristTarget, int elbowTarget) {
  int handStart = handCurrentDegrees;
  int wristStart = wristCurrentDegrees;
  int elbowStart = elbowCurrentDegrees;
  int handDelta = handTarget - handStart;
  int wristDelta = wristTarget - wristStart;
  int elbowDelta = elbowTarget - elbowStart;
  int maxDelta = max(abs(handDelta), max(abs(wristDelta), abs(elbowDelta)));

  if (maxDelta == 0) {
    servos[HAND_SERVO].write(handTarget);
    servos[WRIST_SERVO].write(wristTarget);
    servos[ELBOW_SERVO].write(elbowTarget);
    waitWithStop(SERVO_STEP_DELAY_MS);
    return;
  }

  for (int progress = SERVO_STEP_DEGREES; progress < maxDelta; progress += SERVO_STEP_DEGREES) {
    pollStopCommand();
    if (stopRequested) {
      return;
    }

    handCurrentDegrees = interpolateServoDegrees(handStart, handDelta, progress, maxDelta);
    wristCurrentDegrees = interpolateServoDegrees(wristStart, wristDelta, progress, maxDelta);
    elbowCurrentDegrees = interpolateServoDegrees(elbowStart, elbowDelta, progress, maxDelta);

    servos[HAND_SERVO].write(handCurrentDegrees);
    servos[WRIST_SERVO].write(wristCurrentDegrees);
    servos[ELBOW_SERVO].write(elbowCurrentDegrees);
    waitWithStop(SERVO_STEP_DELAY_MS);
  }

  handCurrentDegrees = handTarget;
  wristCurrentDegrees = wristTarget;
  elbowCurrentDegrees = elbowTarget;
  servos[HAND_SERVO].write(handCurrentDegrees);
  servos[WRIST_SERVO].write(wristCurrentDegrees);
  servos[ELBOW_SERVO].write(elbowCurrentDegrees);
  waitWithStop(SERVO_STEP_DELAY_MS);
}

int interpolateServoDegrees(int startDegrees, int deltaDegrees, int progress, int maxDelta) {
  long numerator = (long)deltaDegrees * progress;
  if (numerator >= 0) {
    return startDegrees + (int)((numerator + maxDelta / 2) / maxDelta);
  }
  return startDegrees + (int)((numerator - maxDelta / 2) / maxDelta);
}

void goHome() {
  setArmServos(HAND_HOME_DEGREES, WRIST_HOME_DEGREES, ELBOW_HOME_DEGREES);
  if (stopRequested) {
    return;
  }
  moveShoulderToDegrees(SHOULDER_HOME_DEGREES);
}

void stopForUnknownShoulderPosition() {
  Serial.println("ERR:SHOULDER_POSITION_UNKNOWN");
  stopRequested = true;
}

void moveWheelSteppersByDegrees(float leftDegrees, float rightDegrees) {
  moveSteppersRelative(leftDegrees, rightDegrees, 0.0);
}

void moveShoulderToDegrees(float targetDegrees) {
  if (!shoulderPositionKnown) {
    stopForUnknownShoulderPosition();
    return;
  }

  float safeTarget = clampFloat(targetDegrees, SHOULDER_MIN_DEGREES, SHOULDER_MAX_DEGREES);
  float delta = safeTarget - shoulderCurrentDegrees;
  moveSteppersRelative(0.0, 0.0, delta);
  if (stopRequested) {
    shoulderPositionKnown = false;
    return;
  }

  shoulderCurrentDegrees = safeTarget;
}

void moveSteppersRelative(float deg0, float deg1, float deg2) {
  float degrees[STEPPER_COUNT] = {deg0, deg1, deg2};
  long steps[STEPPER_COUNT];
  long maxSteps = 0;

  for (int i = 0; i < STEPPER_COUNT; i++) {
    float absoluteDegrees = degrees[i] < 0 ? -degrees[i] : degrees[i];
    steps[i] = (long)(absoluteDegrees * STEPS_PER_DEGREE[i] + 0.5);
    if (steps[i] > maxSteps) {
      maxSteps = steps[i];
    }
    bool direction = degrees[i] >= 0;

    if (STEPPER_DIR_INVERTED[i]) {
      direction = !direction;
    }

    digitalWrite(STEPPER_DIR_PINS[i], direction ? HIGH : LOW);
  }

  for (long stepIndex = 0; stepIndex < maxSteps; stepIndex++) {
    pollStopCommand();
    if (stopRequested) {
      return;
    }

    for (int motor = 0; motor < STEPPER_COUNT; motor++) {
      if (stepIndex < steps[motor]) {
        digitalWrite(STEPPER_STEP_PINS[motor], HIGH);
      }
    }

    delayMicroseconds(STEP_PULSE_US);

    for (int motor = 0; motor < STEPPER_COUNT; motor++) {
      if (stepIndex < steps[motor]) {
        digitalWrite(STEPPER_STEP_PINS[motor], LOW);
      }
    }

    delayMicroseconds(STEP_PAUSE_US);
  }
}

void waitWithStop(unsigned int durationMs) {
  unsigned long start = millis();
  while (millis() - start < durationMs) {
    pollStopCommand();
    if (stopRequested) {
      return;
    }
    delay(5);
  }
}

void pollStopCommand() {
  if (!Serial.available()) {
    return;
  }

  String line = Serial.readStringUntil('\n');
  line.trim();
  line.toUpperCase();
  if (line == "STOP") {
    pendingCommand = "";
    hasPendingCommand = false;
    stopRequested = true;
    Serial.println("OK:STOP");
    return;
  }

  if (line.length() == 0) {
    return;
  }

  if (!hasPendingCommand) {
    pendingCommand = line;
    hasPendingCommand = true;
  } else {
    Serial.println("ERR:PENDING_COMMAND_FULL");
  }
}

float clampFloat(float value, float minValue, float maxValue) {
  if (value < minValue) {
    return minValue;
  }
  if (value > maxValue) {
    return maxValue;
  }
  return value;
}
