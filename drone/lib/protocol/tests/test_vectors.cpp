// Golden vectors of Protocol Spec v0 §6, read from drone/tests/vectors/*.json
// (format in that folder's README.md). The Python twin reads the same files.
// One test per vector, named after it.

#include <gtest/gtest.h>

#include <algorithm>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <map>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "sd/frame.hpp"
#include "sd/hmac.hpp"
#include "sd/messages.hpp"
#include "sd/receiver.hpp"
#include "sd/setpoint.hpp"

namespace {

using json = nlohmann::json;
using Bytes = std::vector<std::uint8_t>;

Bytes from_hex(const std::string &s) {
  EXPECT_EQ(s.size() % 2, 0U) << "odd-length hex: " << s;
  Bytes out;
  for (std::size_t i = 0; i + 1 < s.size(); i += 2) {
    out.push_back(
        static_cast<std::uint8_t>(std::stoi(s.substr(i, 2), nullptr, 16)));
  }
  return out;
}

std::string to_hex(const std::uint8_t *p, std::size_t n) {
  static const char *const kDigits = "0123456789abcdef";
  std::string out;
  for (std::size_t i = 0; i < n; ++i) {
    out += kDigits[p[i] >> 4];
    out += kDigits[p[i] & 0x0f];
  }
  return out;
}

sd::Key key_from_hex(const std::string &s) {
  const Bytes b = from_hex(s);
  EXPECT_EQ(b.size(), sd::kKeySize) << "a key is 32 bytes";
  sd::Key k{};
  std::copy_n(b.begin(), std::min(b.size(), sd::kKeySize), k.bytes);
  return k;
}

const std::map<std::string, sd::MsgType> kTypes = {
    {"HELLO", sd::MsgType::kHello},
    {"HELLO_ACK", sd::MsgType::kHelloAck},
    {"ARM_CONTROL_REQUEST", sd::MsgType::kArmControlRequest},
    {"ARM_CONTROL_ACK", sd::MsgType::kArmControlAck},
    {"RESUME_REQUEST", sd::MsgType::kResumeRequest},
    {"RESUME_ACK", sd::MsgType::kResumeAck},
    {"BYE", sd::MsgType::kBye},
    {"CONFIG_GET", sd::MsgType::kConfigGet},
    {"CONFIG_SET", sd::MsgType::kConfigSet},
    {"CONFIG_ACK", sd::MsgType::kConfigAck},
    {"EVENT", sd::MsgType::kEvent},
    {"SETPOINT", sd::MsgType::kSetpoint},
    {"STATE", sd::MsgType::kState},
};

// README §5 reject reasons.
std::string result_name(sd::RxError e) {
  switch (e) {
  case sd::RxError::kOk:
    return "accept";
  case sd::RxError::kTruncated:
    return "reject:truncated";
  case sd::RxError::kBadMagic:
    return "reject:bad_magic";
  case sd::RxError::kBadVersion:
    return "reject:bad_version";
  case sd::RxError::kBadLength:
    return "reject:bad_length";
  case sd::RxError::kBadTag:
    return "reject:bad_tag";
  }
  return "?";
}

std::string result_name(sd::RxResult r) {
  switch (r) {
  case sd::RxResult::kAccepted:
  case sd::RxResult::kAcceptedGap:
    return "accept";
  case sd::RxResult::kNoSession:
    return "reject:no_session";
  case sd::RxResult::kTruncated:
    return "reject:truncated";
  case sd::RxResult::kBadMagic:
    return "reject:bad_magic";
  case sd::RxResult::kBadVersion:
    return "reject:bad_version";
  case sd::RxResult::kBadLength:
    return "reject:bad_length";
  case sd::RxResult::kBadTag:
    return "reject:bad_tag";
  case sd::RxResult::kWrongSession:
    return "reject:wrong_session";
  case sd::RxResult::kBadType:
    return "reject:bad_type";
  case sd::RxResult::kWrongTransport:
    return "reject:wrong_transport";
  case sd::RxResult::kWrongDirection:
    return "reject:wrong_direction";
  case sd::RxResult::kBadPayloadSize:
    return "reject:bad_payload_size";
  case sd::RxResult::kBadMode:
    return "reject:bad_mode";
  case sd::RxResult::kDuplicateSeq:
    return "reject:duplicate_seq";
  case sd::RxResult::kOldSeq:
    return "reject:old_seq";
  case sd::RxResult::kSeqTooHigh:
    return "reject:seq_too_high";
  }
  return "?";
}

// The Receiver a frame goes to: by its type byte (spec §2), UDP if the type is
// in neither range.
sd::Transport transport_for(const Bytes &buf) {
  sd::Transport t = sd::Transport::kUdp;
  if (buf.size() > 3 && sd::transport_of(buf[3], t)) {
    return t;
  }
  return sd::Transport::kUdp;
}

// The side that receives a frame (spec §2): ground for what the ESP sends,
// ESP otherwise (also for BYE and unknown types).
sd::Role role_for(const Bytes &buf) {
  if (buf.size() > 3 && sd::receives(sd::Role::kGround, buf[3]) &&
      !sd::receives(sd::Role::kEsp, buf[3])) {
    return sd::Role::kGround;
  }
  return sd::Role::kEsp;
}

std::string result_name(sd::HandshakeResult r) {
  switch (r) {
  case sd::HandshakeResult::kAccepted:
    return "accept";
  case sd::HandshakeResult::kTruncated:
    return "reject:truncated";
  case sd::HandshakeResult::kBadMagic:
    return "reject:bad_magic";
  case sd::HandshakeResult::kBadVersion:
    return "reject:bad_version";
  case sd::HandshakeResult::kBadLength:
    return "reject:bad_length";
  case sd::HandshakeResult::kBadTag:
    return "reject:bad_tag";
  case sd::HandshakeResult::kWrongDirection:
    return "reject:wrong_direction";
  case sd::HandshakeResult::kBadPayloadSize:
    return "reject:bad_payload_size";
  case sd::HandshakeResult::kWrongSession:
    return "reject:wrong_session";
  case sd::HandshakeResult::kBadSeq:
    return "reject:bad_seq";
  }
  return "?";
}

template <typename T> T get(const json &f, const char *name) {
  return f.at(name).get<T>();
}

// fields -> payload, with the library's codecs.
Bytes encode_payload(const json &f) {
  const std::string type = f.at("type");
  std::uint8_t buf[sd::kMaxPayload];
  std::size_t n = 0;
  if (type == "HELLO") {
    sd::Hello m{get<std::uint8_t>(f, "proto"), {}};
    const Bytes nonce = from_hex(f.at("ground_nonce"));
    std::copy(nonce.begin(), nonce.end(), m.ground_nonce);
    n = sd::encode_hello(m, buf, sizeof(buf));
  } else if (type == "HELLO_ACK") {
    sd::HelloAck m{get<std::uint32_t>(f, "new_session_id"), {}, {}};
    const Bytes nonce = from_hex(f.at("esp_nonce"));
    std::copy(nonce.begin(), nonce.end(), m.esp_nonce);
    const std::string fw = f.at("fw_version");
    std::copy_n(fw.begin(), std::min(fw.size(), sd::kFwVersionSize),
                m.fw_version);
    n = sd::encode_hello_ack(m, buf, sizeof(buf));
  } else if (type == "ARM_CONTROL_REQUEST") {
    n = sd::encode_u8_payload(get<std::uint8_t>(f, "mask"), buf, sizeof(buf));
  } else if (type == "ARM_CONTROL_ACK" || type == "RESUME_ACK") {
    n = sd::encode_u8_payload(get<std::uint8_t>(f, "result"), buf, sizeof(buf));
  } else if (type == "BYE") {
    n = sd::encode_u8_payload(get<std::uint8_t>(f, "reason"), buf, sizeof(buf));
  } else if (type == "CONFIG_GET" || type == "CONFIG_SET") {
    const sd::ConfigKv m{get<std::uint8_t>(f, "config_key"),
                         get<std::int32_t>(f, "value")};
    n = sd::encode_config_kv(m, buf, sizeof(buf));
  } else if (type == "CONFIG_ACK") {
    const sd::ConfigAck m{get<std::uint8_t>(f, "config_key"),
                          get<std::uint8_t>(f, "result"),
                          get<std::int32_t>(f, "value")};
    n = sd::encode_config_ack(m, buf, sizeof(buf));
  } else if (type == "EVENT") {
    const std::string text = f.at("text");
    sd::Event m{get<std::uint16_t>(f, "code"),
                static_cast<std::uint8_t>(text.size()),
                {}};
    std::copy_n(text.begin(), std::min(text.size(), sd::kEventTextMax), m.text);
    n = sd::encode_event(m, buf, sizeof(buf));
  } else if (type == "SETPOINT") {
    const sd::Setpoint m{
        get<std::uint8_t>(f, "mode"),     get<std::int16_t>(f, "roll"),
        get<std::int16_t>(f, "pitch"),    get<std::int16_t>(f, "yaw_rate"),
        get<std::int16_t>(f, "throttle"), f.value("sp_flags", std::uint8_t{0})};
    n = sd::encode_setpoint(m, buf, sizeof(buf));
  } else if (type == "STATE") {
    const sd::State m{get<std::uint8_t>(f, "esp_state"),
                      get<std::uint8_t>(f, "override_active"),
                      get<std::uint32_t>(f, "last_ctrl_seq"),
                      get<std::uint32_t>(f, "last_ctrl_t_send_us"),
                      get<std::int16_t>(f, "roll"),
                      get<std::int16_t>(f, "pitch"),
                      get<std::uint16_t>(f, "heading"),
                      get<std::uint16_t>(f, "vbat"),
                      get<std::int16_t>(f, "flow_x"),
                      get<std::int16_t>(f, "flow_y"),
                      get<std::uint8_t>(f, "flow_quality"),
                      get<std::uint8_t>(f, "flow_status"),
                      get<std::uint16_t>(f, "rx_rejected"),
                      get<std::uint16_t>(f, "msp_errors")};
    n = sd::encode_state(m, buf, sizeof(buf));
  } else if (type != "RESUME_REQUEST") {
    ADD_FAILURE() << "unknown type " << type;
  }
  return Bytes(buf, buf + n);
}

// payload -> fields, with the library's codecs. Only the payload fields.
json decode_payload(const std::string &type, const std::uint8_t *p,
                    std::size_t len) {
  json f = json::object();
  bool ok = true;
  if (type == "HELLO") {
    sd::Hello m{};
    ok = sd::decode_hello(p, len, m);
    f["proto"] = m.proto;
    f["ground_nonce"] = to_hex(m.ground_nonce, sd::kNonceSize);
  } else if (type == "HELLO_ACK") {
    sd::HelloAck m{};
    ok = sd::decode_hello_ack(p, len, m);
    f["new_session_id"] = m.session_id;
    f["esp_nonce"] = to_hex(m.esp_nonce, sd::kNonceSize);
    f["fw_version"] =
        std::string(m.fw_version, strnlen(m.fw_version, sd::kFwVersionSize));
  } else if (type == "ARM_CONTROL_REQUEST" || type == "ARM_CONTROL_ACK" ||
             type == "RESUME_ACK" || type == "BYE") {
    std::uint8_t v = 0;
    ok = sd::decode_u8_payload(p, len, v);
    f[type == "ARM_CONTROL_REQUEST" ? "mask"
      : type == "BYE"               ? "reason"
                                    : "result"] = v;
  } else if (type == "RESUME_REQUEST") {
    ok = len == 0;
  } else if (type == "CONFIG_GET" || type == "CONFIG_SET") {
    sd::ConfigKv m{};
    ok = sd::decode_config_kv(p, len, m);
    f["config_key"] = m.key;
    f["value"] = m.value;
  } else if (type == "CONFIG_ACK") {
    sd::ConfigAck m{};
    ok = sd::decode_config_ack(p, len, m);
    f["config_key"] = m.key;
    f["result"] = m.result;
    f["value"] = m.value;
  } else if (type == "EVENT") {
    sd::Event m{};
    ok = sd::decode_event(p, len, m);
    f["code"] = m.code;
    f["text"] = std::string(m.text, m.text_len);
  } else if (type == "SETPOINT") {
    sd::Setpoint m{};
    ok = sd::decode_setpoint(p, len, m);
    f["mode"] = m.mode;
    f["roll"] = m.roll;
    f["pitch"] = m.pitch;
    f["yaw_rate"] = m.yaw_rate;
    f["throttle"] = m.throttle;
    f["sp_flags"] = m.flags;
  } else if (type == "STATE") {
    sd::State m{};
    ok = sd::decode_state(p, len, m);
    f["esp_state"] = m.esp_state;
    f["override_active"] = m.override_active;
    f["last_ctrl_seq"] = m.last_ctrl_seq;
    f["last_ctrl_t_send_us"] = m.last_ctrl_t_send_us;
    f["roll"] = m.roll;
    f["pitch"] = m.pitch;
    f["heading"] = m.heading;
    f["vbat"] = m.vbat;
    f["flow_x"] = m.flow_x;
    f["flow_y"] = m.flow_y;
    f["flow_quality"] = m.flow_quality;
    f["flow_status"] = m.flow_status;
    f["rx_rejected"] = m.rx_rejected;
    f["msp_errors"] = m.msp_errors;
  }
  EXPECT_TRUE(ok) << "payload of " << type << " not decoded";
  return f;
}

std::vector<json> load_vectors() {
  std::vector<std::filesystem::path> files;
  for (const auto &entry : std::filesystem::directory_iterator(SD_VECTOR_DIR)) {
    if (entry.path().extension() == ".json") {
      files.push_back(entry.path());
    }
  }
  std::sort(files.begin(), files.end());
  std::vector<json> vectors;
  for (const auto &path : files) {
    std::ifstream in(path);
    const json data = json::parse(in);
    if (data.is_array()) {
      vectors.insert(vectors.end(), data.begin(), data.end());
    } else {
      vectors.push_back(data);
    }
  }
  return vectors;
}

// One vector. GoogleTest prints only its name, not the whole JSON.
struct Golden {
  json v;
};

void PrintTo(const Golden &g, std::ostream *os) { *os << g.v.at("name"); }

const std::vector<Golden> &vectors() {
  static const std::vector<Golden> all = [] {
    std::vector<Golden> out;
    for (auto &v : load_vectors()) {
      out.push_back({std::move(v)});
    }
    return out;
  }();
  return all;
}

class Vector : public ::testing::TestWithParam<Golden> {};

TEST_P(Vector, Check) {
  const json &v = GetParam().v;
  const sd::Key key = key_from_hex(v.at("key"));

  // Group 2: the key is derived from the PSK and the nonces: the session key,
  // then the key of one direction ("g2e" or "e2g", ADR-002, #150).
  // Group 2: the key is a direction key derived with derive_keys() from the PSK
  // and the nonces ("g2e" ground to ESP, "e2g" ESP to ground; #150, #152).
  ASSERT_EQ(v.contains("direction"), v.contains("psk"))
      << "psk and direction come together";
  if (v.contains("psk")) {
    const Bytes ground = from_hex(v.at("ground_nonce"));
    const Bytes esp = from_hex(v.at("esp_nonce"));
    ASSERT_EQ(ground.size(), sd::kNonceSize) << "ground_nonce is 16 bytes";
    ASSERT_EQ(esp.size(), sd::kNonceSize) << "esp_nonce is 16 bytes";
    const std::string direction = v.at("direction");
    ASSERT_TRUE(direction == "g2e" || direction == "e2g") << direction;
    const sd::DirectionKeys keys =
        sd::derive_keys(key_from_hex(v.at("psk")), ground.data(), esp.data());
    const sd::Key &derived =
        direction == "g2e" ? keys.ground_to_esp : keys.esp_to_ground;
    EXPECT_EQ(to_hex(derived.bytes, sd::kKeySize), v.at("key"));
  }

  // One Receiver per side and transport (spec §2, §4 rule 3): UDP starts at 0,
  // TCP at 1 (HELLO used seq 1). A step says its "transport" (default udp)
  // and "role" (default esp); a single frame goes to the Receiver of the side
  // and transport of its type.
  sd::Receiver esp_udp(sd::Role::kEsp, sd::Transport::kUdp);
  sd::Receiver esp_tcp(sd::Role::kEsp, sd::Transport::kTcp);
  sd::Receiver ground_udp(sd::Role::kGround, sd::Transport::kUdp);
  sd::Receiver ground_tcp(sd::Role::kGround, sd::Transport::kTcp);
  sd::Receiver *all[] = {&esp_udp, &esp_tcp, &ground_udp, &ground_tcp};
  const auto pick = [&](sd::Role role, sd::Transport t) -> sd::Receiver & {
    if (role == sd::Role::kEsp) {
      return t == sd::Transport::kTcp ? esp_tcp : esp_udp;
    }
    return t == sd::Transport::kTcp ? ground_tcp : ground_udp;
  };
  const bool with_session = v.contains("state");
  if (with_session) {
    const json &state = v["state"];
    ASSERT_EQ(state.value("last_ctrl_seq", 0), 0)
        << "vectors start from a fresh session";
    ASSERT_EQ(state.value("last_tcp_seq", 1), 1) << "TCP starts after HELLO";
    for (sd::Receiver *rx : all) {
      ASSERT_TRUE(rx->start_session(state.at("current_session"), key));
    }
  }

  // Groups 5, 6 and 8: several frames, the result after each one.
  if (v.contains("steps")) {
    int i = 0;
    for (const auto &step : v["steps"]) {
      const std::string transport = step.value("transport", "udp");
      const std::string role = step.value("role", "esp");
      ASSERT_TRUE(transport == "udp" || transport == "tcp") << transport;
      ASSERT_TRUE(role == "esp" || role == "ground") << role;
      sd::Receiver &to =
          pick(role == "ground" ? sd::Role::kGround : sd::Role::kEsp,
               transport == "tcp" ? sd::Transport::kTcp : sd::Transport::kUdp);
      const Bytes buf = from_hex(step.at("bytes"));
      sd::Frame f{};
      EXPECT_EQ(result_name(to.receive(buf.data(), buf.size(), f)),
                step.at("expect"))
          << "step " << i++ << " (" << role << ", " << transport << ")";
    }
    const json final = v.value("final", json::object());
    if (final.contains("last_ctrl_seq")) {
      EXPECT_EQ(esp_udp.last_seq(), final["last_ctrl_seq"]);
    }
    if (final.contains("last_tcp_seq")) {
      EXPECT_EQ(esp_tcp.last_seq(), final["last_tcp_seq"]);
    }
    // Totals over every Receiver.
    std::uint32_t rejected = 0;
    std::uint32_t gaps = 0;
    for (const sd::Receiver *rx : all) {
      rejected += rx->counters().rejected;
      gaps += rx->seq_counters().gaps;
    }
    if (final.contains("rx_rejected")) {
      EXPECT_EQ(rejected, final["rx_rejected"]);
    }
    if (final.contains("gaps")) {
      EXPECT_EQ(gaps, final["gaps"]);
    }
    return;
  }

  const Bytes buf = from_hex(v.at("bytes"));
  sd::Frame frame{};

  // Group 9: a handshake frame checked by one side before any session.
  if (v.contains("handshake")) {
    const std::string side = v.at("handshake");
    ASSERT_TRUE(side == "esp" || side == "ground") << side;
    const sd::Role role = side == "ground" ? sd::Role::kGround : sd::Role::kEsp;
    ASSERT_EQ(result_name(sd::receive_handshake(role, buf.data(), buf.size(),
                                                key, frame)),
              v.at("expect"));
    if (v.at("expect") != "accept") {
      EXPECT_EQ(frame.payload, nullptr) << "out written on a reject";
      return;
    }
  } else {
    sd::Receiver &own = pick(role_for(buf), transport_for(buf));
    const std::string got =
        with_session
            ? result_name(own.receive(buf.data(), buf.size(), frame))
            : result_name(sd::decode_frame(buf.data(), buf.size(), key, frame));
    ASSERT_EQ(got, v.at("expect"));

    // A rejected frame is also refused by a Receiver and counted.
    if (got != "accept") {
      if (!with_session) {
        ASSERT_TRUE(own.start_session(0x12345678, key));
        sd::Frame ignored{};
        EXPECT_EQ(result_name(own.receive(buf.data(), buf.size(), ignored)),
                  got);
      }
      EXPECT_EQ(own.counters().rejected, 1U);
      EXPECT_EQ(own.counters().accepted, 0U);
    }
  }

  if (!v.contains("fields")) {
    return;
  }
  const json &fields = v["fields"];
  const std::string type = fields.at("type");

  // Encode: fields -> exactly bytes.
  const Bytes payload = encode_payload(fields);
  const sd::Header h{static_cast<std::uint8_t>(kTypes.at(type)),
                     fields.value("flags", std::uint8_t{0}),
                     0,
                     fields.at("session_id"),
                     fields.at("seq"),
                     fields.at("t_send_us")};
  std::uint8_t out[sd::kMaxFrame];
  const std::size_t n = sd::encode_frame(h, payload.data(), payload.size(), key,
                                         out, sizeof(out));
  EXPECT_EQ(to_hex(out, n), v.at("bytes")) << "encode";

  // Decode: bytes -> exactly fields.
  EXPECT_EQ(frame.header.type, static_cast<std::uint8_t>(kTypes.at(type)));
  EXPECT_EQ(frame.header.flags, fields.value("flags", 0));
  EXPECT_EQ(frame.header.session_id, fields.at("session_id"));
  EXPECT_EQ(frame.header.seq, fields.at("seq"));
  EXPECT_EQ(frame.header.t_send_us, fields.at("t_send_us"));
  const json decoded =
      decode_payload(type, frame.payload, frame.header.payload_len);
  for (const auto &item : decoded.items()) {
    const json want = item.key() == "sp_flags"
                          ? fields.value("sp_flags", json(0))
                          : fields.at(item.key());
    EXPECT_EQ(item.value(), want) << "decode " << item.key();
  }
  // Every key in fields is checked: header keys plus what the decoder gives.
  for (const auto &item : fields.items()) {
    const bool header_key = item.key() == "type" || item.key() == "flags" ||
                            item.key() == "session_id" || item.key() == "seq" ||
                            item.key() == "t_send_us";
    EXPECT_TRUE(header_key || decoded.contains(item.key()))
        << "field '" << item.key() << "' is not part of " << type;
  }

  // Group 6: the RC channels sent to the FC.
  if (v.contains("channels")) {
    sd::Setpoint sp{};
    ASSERT_TRUE(
        sd::decode_setpoint(frame.payload, frame.header.payload_len, sp));
    std::uint16_t ch[sd::kRcChannels];
    sd::setpoint_to_rc(sp, ch);
    EXPECT_EQ(std::vector<std::uint16_t>(ch, ch + sd::kRcChannels),
              v["channels"].get<std::vector<std::uint16_t>>());
  }
}

INSTANTIATE_TEST_SUITE_P(Golden, Vector, ::testing::ValuesIn(vectors()),
                         [](const ::testing::TestParamInfo<Golden> &info) {
                           return info.param.v.at("name").get<std::string>();
                         });

} // namespace

TEST(Vectors, FilesLoadedAndNamesUnique) {
  std::vector<std::string> names;
  for (const auto &g : vectors()) {
    names.push_back(g.v.at("name"));
  }
  EXPECT_GE(names.size(), 68U);
  std::sort(names.begin(), names.end());
  EXPECT_EQ(std::adjacent_find(names.begin(), names.end()), names.end())
      << "vector names must be unique";
}
