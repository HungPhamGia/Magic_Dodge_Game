#include <Wire.h>
#include <WiFi.h>
#include <WiFiUdp.h>

const char* SSID = "MagicWand";
const char* PASS = "wand1234";
const int   UPORT = 4210;

const int MPU = 0x68, LED = 2, BTN = 14;

WiFiUDP udp;
IPAddress client;
bool haveClient = false;

float gxBias = 0, gyBias = 0, gzBias = 0, ax = 0, ay = 0;
bool pen = false;
unsigned long tPrev = 0;

void mpuWrite(uint8_t r, uint8_t v) {
  Wire.beginTransmission(MPU); Wire.write(r); Wire.write(v); Wire.endTransmission(true);
}
void readGyro(float &gx, float &gy, float &gz) {
  Wire.beginTransmission(MPU); Wire.write(0x43); Wire.endTransmission(false);
  Wire.requestFrom(MPU, 6, true);
  int16_t rx = Wire.read() << 8 | Wire.read();
  int16_t ry = Wire.read() << 8 | Wire.read();
  int16_t rz = Wire.read() << 8 | Wire.read();
  gx = rx / 65.5f; gy = ry / 65.5f; gz = rz / 65.5f;
}
void send(const char* s) {
  if (!haveClient) return;
  udp.beginPacket(client, UPORT);
  udp.write((const uint8_t*)s, strlen(s));
  udp.endPacket();
}
void calibrate() {
  float sx = 0, sy = 0, sz = 0, gx, gy, gz;
  for (int i = 0; i < 500; i++) { readGyro(gx, gy, gz); sx += gx; sy += gy; sz += gz; delay(3); }
  gxBias = sx / 500.0f; gyBias = sy / 500.0f; gzBias = sz / 500.0f;
  char buf[80];
  snprintf(buf, sizeof(buf), "# bias gx %.2f gy %.2f gz %.2f\n", gxBias, gyBias, gzBias);
  Serial.print(buf); send(buf);
  tPrev = millis();
}

void setup() {
  Serial.begin(115200);
  pinMode(LED, OUTPUT);
  pinMode(BTN, INPUT_PULLUP);
  Wire.begin(32, 33, 100000);
  mpuWrite(0x6B, 0x80); delay(100);
  mpuWrite(0x6B, 0x01); delay(100);
  mpuWrite(0x1A, 0x03);
  mpuWrite(0x1B, 0x08);
  delay(200);

  WiFi.softAP(SSID, PASS);
  udp.begin(UPORT);
  Serial.print("# AP up, esp ip ");
  Serial.println(WiFi.softAPIP());

  Serial.println("# hold still, calibrating");
  calibrate();
  Serial.println("# ready");
}

void loop() {
  if (udp.parsePacket()) {
    char c = 0;
    if (udp.available()) c = udp.read();
    while (udp.available()) udp.read();
    client = udp.remoteIP(); haveClient = true;
    if (c == 'z') { ax = 0; ay = 0; }
    if (c == 'b') { send("# recalibrating\n"); calibrate(); }
  }

  if (millis() - tPrev < 10) return;   // 100 Hz
  float dt = (millis() - tPrev) / 1000.0f;
  tPrev = millis();

  bool now = (digitalRead(BTN) == LOW);
  if (now && !pen) { ax = 0; ay = 0; }
  pen = now;
  digitalWrite(LED, pen);

  float gx, gy, gz; readGyro(gx, gy, gz);
  gx -= gxBias; gy -= gyBias; gz -= gzBias;
  (void)gy;

  if (pen) {
    if (fabs(gx) < 3) gx = 0;
    if (fabs(gz) < 3) gz = 0;
    ax += -gz * dt; ay += -gx * dt;
  }

  char buf[48];
  snprintf(buf, sizeof(buf), "P,%.2f,%.2f,%d\n", ax, ay, pen ? 1 : 0);
  send(buf);
}