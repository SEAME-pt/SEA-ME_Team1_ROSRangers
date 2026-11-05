#include "sd/receiver.hpp"

#include "sd/messages.hpp"

namespace sd {

bool Receiver::start_session(std::uint32_t session_id, const Key &session_key,
                             std::uint32_t last_seq) {
  if (session_id == 0 || last_seq >= SeqWindow::kMaxSeq) {
    return false;
  }
  key_ = session_key;
  session_id_ = session_id;
  in_session_ = true;
  window_.reset(last_seq);
  return true;
}

void Receiver::end_session() {
  key_ = {};
  session_id_ = 0;
  in_session_ = false;
  window_.reset();
}

RxResult Receiver::reject(RxResult r) {
  ++counters_.rejected;
  switch (r) {
  case RxResult::kNoSession:
    ++counters_.no_session;
    break;
  case RxResult::kTruncated:
    ++counters_.truncated;
    break;
  case RxResult::kBadMagic:
    ++counters_.bad_magic;
    break;
  case RxResult::kBadVersion:
    ++counters_.bad_version;
    break;
  case RxResult::kBadLength:
    ++counters_.bad_length;
    break;
  case RxResult::kBadTag:
    ++counters_.bad_tag;
    break;
  case RxResult::kWrongSession:
    ++counters_.wrong_session;
    break;
  case RxResult::kBadType:
    ++counters_.bad_type;
    break;
  case RxResult::kBadPayloadSize:
    ++counters_.bad_payload_size;
    break;
  case RxResult::kBadMode:
    ++counters_.bad_mode;
    break;
  case RxResult::kDuplicateSeq:
    ++counters_.duplicate_seq;
    break;
  case RxResult::kOldSeq:
    ++counters_.old_seq;
    break;
  case RxResult::kSeqTooHigh:
    ++counters_.seq_too_high;
    break;
  case RxResult::kAccepted:
  case RxResult::kAcceptedGap:
    break;
  }
  return r;
}

RxResult Receiver::receive(const std::uint8_t *buf, std::size_t len,
                           Frame &out) {
  if (!in_session_) {
    return reject(RxResult::kNoSession);
  }

  // Decoded into a local frame: `out` is written only for an accepted frame,
  // so a rejected or replayed payload can never be used by mistake.
  Frame frame{};

  // Rule 1: length, magic, version, tag.
  switch (decode_frame(buf, len, key_, frame)) {
  case RxError::kOk:
    break;
  case RxError::kTruncated:
    return reject(RxResult::kTruncated);
  case RxError::kBadMagic:
    return reject(RxResult::kBadMagic);
  case RxError::kBadVersion:
    return reject(RxResult::kBadVersion);
  case RxError::kBadLength:
    return reject(RxResult::kBadLength);
  case RxError::kBadTag:
    return reject(RxResult::kBadTag);
  }

  // Rule 2: the current session only.
  if (frame.header.session_id != session_id_) {
    return reject(RxResult::kWrongSession);
  }

  // Type, size and mode (spec §3, REQ-COM-001/002). Before the window, so a
  // malformed frame leaves last_seq unchanged (REQ-COM-004).
  switch (check_payload(frame.header.type, frame.payload,
                        frame.header.payload_len)) {
  case PayloadCheck::kOk:
    break;
  case PayloadCheck::kUnknownType:
    return reject(RxResult::kBadType);
  case PayloadCheck::kBadSize:
    return reject(RxResult::kBadPayloadSize);
  case PayloadCheck::kBadMode:
    return reject(RxResult::kBadMode);
  }

  // Rule 3: newer than the last accepted message (ADR-003).
  switch (window_.accept(frame.header.seq)) {
  case SeqResult::kAccepted:
    ++counters_.accepted;
    out = frame;
    return RxResult::kAccepted;
  case SeqResult::kAcceptedGap:
    ++counters_.accepted;
    out = frame;
    return RxResult::kAcceptedGap;
  case SeqResult::kDuplicate:
    return reject(RxResult::kDuplicateSeq);
  case SeqResult::kOld:
    return reject(RxResult::kOldSeq);
  case SeqResult::kNeedNewSession:
    return reject(RxResult::kSeqTooHigh);
  }
  // Unreachable: every SeqResult value is handled above.
  return reject(RxResult::kOldSeq);
}

} // namespace sd
