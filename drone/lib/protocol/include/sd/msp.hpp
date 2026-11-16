#pragma once

// MSP v1 between the ESP and the flight controller (ADR-009, MSP Cheat Sheet).
// The ESP talks to the FC only through this file: it can build a request only
// for a command on the allow-list, so a forbidden command (MSP_SET_MOTOR,
// MSP_EEPROM_WRITE, MSP_REBOOT, ...) can never reach the FC (#101).
//
// Frame: '$' 'M' dir len cmd payload[len] checksum, where dir is '<' for a
// request, '>' for a reply and '!' for an error reply, and checksum is the XOR
// of len, cmd and every payload byte. Payload integers are little-endian.

#include <cstddef>
#include <cstdint>

namespace sd {
namespace msp {

// Command IDs (MSP Cheat Sheet §3).
constexpr std::uint8_t kApiVersion = 1;
constexpr std::uint8_t kStatus = 101;
constexpr std::uint8_t kRc = 105;
constexpr std::uint8_t kAttitude = 108;
constexpr std::uint8_t kAnalog = 110;
constexpr std::uint8_t kSetRawRc = 200;
// ESP-originated only (ADR-024 upload lock): never allowed(), never built
// from network data. Only encode_set_arming_disabled() builds it.
constexpr std::uint8_t kSetArmingDisabled = 99;

constexpr std::size_t kHeaderSize = 5;  // '$' 'M' dir len cmd
constexpr std::size_t kMaxPayload = 64; // the largest reply read (MSP_STATUS)
constexpr std::size_t kMaxFrame = kHeaderSize + kMaxPayload + 1;
constexpr std::size_t kRcChannels = 8;

// The commands the ESP may send (MSP Cheat Sheet, gateway allow-list):
// MSP_API_VERSION, MSP_STATUS, MSP_RC, MSP_ATTITUDE, MSP_ANALOG and
// MSP_SET_RAW_RC. Every other command is refused, every MSP_SET_* but 200 too.
bool allowed(std::uint8_t cmd);

// Builds a read request into `out`. Returns its size, or 0 if `cmd` is not
// allowed, `cmd` is MSP_SET_RAW_RC (the one write: only encode_set_raw_rc()
// builds it, always with exactly 8 channels), a pointer is null (payload may be
// null only when len is 0), len is over kMaxPayload, or out_cap is too small.
std::size_t encode_request(std::uint8_t cmd, const std::uint8_t *payload,
                           std::size_t len, std::uint8_t *out,
                           std::size_t out_cap);

// MSP_SET_RAW_RC with 8 channels in AETR order (spec §5), 16-byte payload. The
// only way to build the one write command on the allow-list (#160 review).
std::size_t encode_set_raw_rc(const std::uint16_t channels[kRcChannels],
                              std::uint8_t *out, std::size_t out_cap);

// MSP_SET_ARMING_DISABLED (99) with value 1 (lock) or 0 (release), 1-byte
// payload. Internal to the ESP: only the ADR-024 OTA lock and release call it
// (sd/ota_lock.hpp). Betaflight disarms an armed FC when it gets value 1, so
// the lock sends it only after a fresh MSP_STATUS said disarmed.
std::size_t encode_set_arming_disabled(bool disable, std::uint8_t *out,
                                       std::size_t out_cap);

// One reply from the FC.
struct Reply {
  std::uint8_t cmd;
  bool error; // '!' instead of '>': the FC refused or does not know cmd
  std::uint8_t len;
  std::uint8_t payload[kMaxPayload];
};

// Reads the FC's bytes one at a time and finds the replies in them. Bytes
// before a '$', requests ('<'), bad checksums and replies longer than
// kMaxPayload are skipped and counted; it never waits for more than one
// frame, so a stray byte cannot block it.
class Parser {
public:
  enum class Event : std::uint8_t {
    kNone = 0,    // nothing complete yet
    kReply,       // reply() holds a complete, checked reply
    kBadChecksum, // a frame was dropped: checksum wrong
    kTooLong,     // a frame was dropped: len > kMaxPayload
  };

  Event feed(std::uint8_t byte);
  const Reply &reply() const { return reply_; }

private:
  enum class State : std::uint8_t {
    kIdle,
    kM,
    kDir,
    kLen,
    kCmd,
    kPayload,
    kChecksum,
    kSkip,
  };

  State state_ = State::kIdle;
  Reply reply_ = {};
  std::uint8_t pending_len_ = 0;
  std::uint8_t pos_ = 0;
  std::uint8_t checksum_ = 0;
  std::uint16_t skip_ = 0;
  bool error_ = false;
  bool too_long_ = false;
};

// MSP_ATTITUDE: roll and pitch in 0.1 deg, heading 0..359 deg.
struct Attitude {
  std::int16_t roll;
  std::int16_t pitch;
  std::uint16_t heading;
};

// MSP_ANALOG: battery voltage in 0.01 V (the last field, Betaflight 4.x and
// 2026.x; the first byte is the legacy 0.1 V value).
struct Analog {
  std::uint16_t vbat;
};

// MSP_STATUS: the ARM state is bit 0 of the flight mode flags (BOXARM is always
// the first active box). The arming-disable flags follow the extra flight mode
// bytes; ARMING_DISABLED_MSP is bit 16 (ADR-024).
struct Status {
  bool armed;
  std::uint32_t arming_disable_flags;
};
constexpr std::uint32_t kArmingDisabledMsp = 1U << 16;

// Each returns false for an error reply, another command, or a payload too
// short for the fields read.
bool decode_attitude(const Reply &reply, Attitude &out);
bool decode_analog(const Reply &reply, Analog &out);
bool decode_status(const Reply &reply, Status &out);

} // namespace msp
} // namespace sd
