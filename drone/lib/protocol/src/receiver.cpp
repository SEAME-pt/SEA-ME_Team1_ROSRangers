#include "sd/receiver.hpp"

namespace sd {

Receiver::Receiver(Role role, Transport transport)
    : role_(role), transport_(transport) {}

Receiver::~Receiver() { secure_zero(&key_, sizeof(key_)); }

bool Receiver::start_session(std::uint32_t session_id, const Key &rx_key) {
  if (session_id == 0) {
    return false;
  }
  key_ = rx_key;
  session_id_ = session_id;
  in_session_ = true;
  window_.reset(transport_ == Transport::kTcp ? 1 : 0);
  return true;
}

void Receiver::end_session() {
  secure_zero(&key_, sizeof(key_));
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
  case RxResult::kWrongTransport:
    ++counters_.wrong_transport;
    break;
  case RxResult::kWrongDirection:
    ++counters_.wrong_direction;
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

  // Type, transport, direction, size and mode (spec §2, §3, REQ-COM-001/002).
  // Before the window, so a malformed frame leaves last_seq unchanged
  // (REQ-COM-004).
  const PayloadCheck check =
      check_payload(frame.header.type, frame.payload, frame.header.payload_len);
  if (check == PayloadCheck::kUnknownType) {
    return reject(RxResult::kBadType);
  }
  // Both transports use the same direction key: without this, a frame captured
  // on TCP could be replayed on UDP, or the other way.
  Transport transport = Transport::kUdp;
  if (!transport_of(frame.header.type, transport) || transport != transport_) {
    return reject(RxResult::kWrongTransport);
  }
  // Direction (#152): only what the other side sends. A node's own frame sent
  // back to it is refused here even if both ends got the keys wrong.
  if (!receives(role_, frame.header.type)) {
    return reject(RxResult::kWrongDirection);
  }
  switch (check) {
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

HandshakeResult receive_handshake(Role role, const std::uint8_t *buf,
                                  std::size_t len, const Key &psk, Frame &out) {
  Frame frame{};
  switch (decode_frame(buf, len, psk, frame)) {
  case RxError::kOk:
    break;
  case RxError::kTruncated:
    return HandshakeResult::kTruncated;
  case RxError::kBadMagic:
    return HandshakeResult::kBadMagic;
  case RxError::kBadVersion:
    return HandshakeResult::kBadVersion;
  case RxError::kBadLength:
    return HandshakeResult::kBadLength;
  case RxError::kBadTag:
    return HandshakeResult::kBadTag;
  }
  const MsgType expected =
      role == Role::kEsp ? MsgType::kHello : MsgType::kHelloAck;
  if (frame.header.type != static_cast<std::uint8_t>(expected)) {
    return HandshakeResult::kWrongDirection;
  }
  const std::size_t size = role == Role::kEsp ? kHelloSize : kHelloAckSize;
  if (frame.header.payload_len != size) {
    return HandshakeResult::kBadPayloadSize;
  }
  if (role == Role::kEsp) {
    if (frame.header.session_id != 0) {
      return HandshakeResult::kWrongSession;
    }
  } else {
    HelloAck ack{};
    if (!decode_hello_ack(frame.payload, frame.header.payload_len, ack) ||
        frame.header.session_id == 0 ||
        ack.session_id != frame.header.session_id) {
      return HandshakeResult::kWrongSession;
    }
  }
  if (frame.header.seq != 1) {
    return HandshakeResult::kBadSeq;
  }
  out = frame;
  return HandshakeResult::kAccepted;
}

} // namespace sd
