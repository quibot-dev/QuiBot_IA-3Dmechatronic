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
