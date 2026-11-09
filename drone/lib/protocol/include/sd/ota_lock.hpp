#pragma once
// ADR-024 OTA gate (D2) and upload lock, as testable logic. The firmware calls
// ArduinoOTA.handle() only while ota_gate_open() is true, takes the lock in
// ArduinoOTA's start callback, and releases it at every boot and after a
// failed upload (from the main loop, never from a callback).
//
// The lock and release talk to the FC directly and block (at most a few
// hundred ms): they run only when the main loop is stopped anyway (the start
// callback) or rarely (boot, after a failed upload, both with no session).

#include <cstddef>
#include <cstdint>

#include "sd/fc_link.hpp"
#include "sd/link.hpp"
#include "sd/msp.hpp"

namespace sd {

constexpr std::uint32_t kOtaStatusMaxAgeMs = 500; // ADR-024 D2
constexpr std::uint32_t kOtaReplyTimeoutMs = 100; // per request to the FC
// Before step 1 the UART must be quiet this long, so a late reply to an
// earlier poll (FcLink waits kReplyTimeoutMs = 50 ms for one) can never be
// taken for the reply to the lock's own request (review of #102 part 3).
constexpr std::uint32_t kOtaQuietMs = 60;
constexpr std::uint32_t kOtaQuietMaxMs = 500; // the FC never goes quiet: abort

// D2: no ground session (not even a pending one, State Machine §1), and the
// FC is disarmed per an MSP_STATUS under 500 ms old. Unknown means closed.
bool ota_gate_open(const Link &link, const FcLink &fc, std::uint32_t now_ms);

// The FC UART for the blocking exchanges below.
class FcUart {
public:
  virtual void write(const std::uint8_t *buf, std::size_t len) = 0;
  // One byte, or -1 when none is waiting (the firmware yields here).
  virtual int read() = 0;
  virtual std::uint32_t now_ms() = 0;
  // Throw away buffered bytes: replies to older requests are never used.
  virtual void discard_input() = 0;

protected:
  ~FcUart() = default;
};

enum class OtaLockResult : std::uint8_t {
  kLocked = 0, // ARMING_DISABLED_MSP read back: the upload may go on
  kArmed,      // step 1: the FC is armed; value 1 was not sent
  kNoReply,    // step 1: no fresh MSP_STATUS, or the UART never went quiet;
               // value 1 was not sent
  kNotLocked,  // step 2: the FC did not confirm the lock
};

// ADR-024 upload lock. Step 1: a fresh MSP_STATUS (buffered replies
// discarded, then kOtaQuietMs of silence, so only the reply to this request
// counts) must say disarmed. Step 2: MSP_SET_ARMING_DISABLED 1, then
// MSP_STATUS must show ARMING_DISABLED_MSP. On anything but kLocked the
// caller aborts the upload (ESP.restart()).
OtaLockResult ota_lock(FcUart &uart);

// MSP_SET_ARMING_DISABLED 0. Harmless at any time: value 0 never arms or
// disarms. True when the FC acknowledged it.
bool ota_release(FcUart &uart);

} // namespace sd
