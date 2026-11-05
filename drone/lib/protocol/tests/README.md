# Protocol library tests, in plain words

Every test of `drone/lib/protocol`: what it does and why it matters.
`scripts/protocol_tests.py` reads this file and prints these lines next to each test as it runs, so **add a row here when you add a test**.

Words used below:
- **Frame:** one message as bytes. It is a header (20 bytes) + content + seal (8 bytes).
- **Seal:** the tag (truncated HMAC). Only someone with the key can make a correct seal.
- **seq:** the message number. It must always go up.
- **Session:** one connection between the ground PC and the drone, with its own key.

## Frame: building and reading frames
Turns a message into bytes and back.

| Test | What it does | Why it matters |
|---|---|---|
| `Frame.EncodesTheReferenceSetpointByteForByte` | Builds the reference SETPOINT (roll 100, pitch −200, throttle 500, seq 7) and compares all 38 bytes with the known-correct bytes | The Python ground code must make exactly the same bytes, or the two sides cannot understand each other |
| `Frame.DecodesTheReferenceSetpoint` | Reads those 38 bytes back and checks every field: type, session, seq, time, roll, pitch, yaw, throttle | Reading must give back exactly what was sent |
| `Frame.EmptyPayloadRoundTrip` | Builds and reads a message with no content (RESUME_REQUEST): 28 bytes, content length 0 | Some commands carry no data and must still work |
| `Frame.MaxPayloadRoundTripAndOneMoreRefused` | 256 bytes of content (the maximum) works; 257 is refused | The ESP has fixed-size memory; a bigger message would overflow it |
| `Frame.EncodeRefusesSmallBuffer` | Building into a space 1 byte too small returns 0 (nothing built) | The code never writes past the end of its memory |
| `Frame.EncodeRefusesNullPointers` | Building with missing content (null pointer but length 10) or no output space returns 0 | A programming mistake cannot crash the ESP |

## FrameReject: frames that must be refused
Broken or forged frames are dropped. Each test checks one way a frame can be wrong.

| Test | What it does | Why it matters |
|---|---|---|
| `FrameReject.PayloadBitFlipped` | Changes roll from 100 to 101 (one bit) but keeps the old seal → refused (bad seal) | Radio noise or an attacker changing a value is detected |
| `FrameReject.TagBitFlipped` | Changes one bit of the seal itself → refused | A damaged seal is never accepted |
| `FrameReject.WrongKey` | Checks a correct frame with a key that differs in one bit → refused | Without our exact key, nobody can make an accepted frame |
| `FrameReject.EveryHeaderOrPayloadBitIsCovered` | Flips each of the 240 header and content bits, one at a time; all 240 must be refused | No part of the message is left unprotected |
| `FrameReject.WrongMagicWithValidTag` | A frame starting with "SE" instead of "SD", even with a correct seal → refused (bad magic) | Traffic that is not our protocol is dropped at once, before the slower seal check |
| `FrameReject.WrongVersion` | Version byte 2 instead of 1 → refused | A PC running another protocol version is never misunderstood |
| `FrameReject.PayloadLenOver256` | The header claims 257 bytes of content → refused (bad length) | A false length cannot make the ESP read past its memory |
| `FrameReject.LengthMismatchExtraBytes` | One extra byte after the frame → refused | The size must match exactly |
| `FrameReject.TruncatedFrame` | The frame is missing its last byte, has only 10 bytes, or is empty → refused (truncated) | Half a message is never used |

## FrameSize: cutting the TCP stream
TCP delivers one long stream of bytes. This finds where each frame ends.

| Test | What it does | Why it matters |
|---|---|---|
| `FrameSize.ReadsTcpFrameLengthFromHeader` | From the 20-byte header it works out the full frame size (38). With fewer than 20 bytes, or a length over 256, it returns 0 | This is how the ESP separates commands that arrive stuck together on TCP |

## FrameFuzz: random garbage
"Fuzzing" means throwing lots of random input at the code. The random numbers are fixed (seed 97 and 99), so every run is identical.

| Test | What it does | Why it matters |
|---|---|---|
| `FrameFuzz.RandomBytesNeverCrashAndAreRejected` | 20,000 random byte strings (half start with a valid "SD" and version). None may be accepted and nothing may crash | REQ-COM-005: garbage from the Wi-Fi must never crash or fool the ESP |
| `FrameFuzz.MutatedValidFramesAreRejected` | Corrupts one random byte of the good frame, 5,000 times. Every copy must be refused | Any damage anywhere is caught |

## Sha256: the hash function
SHA-256 turns any data into a 32-byte fingerprint. The seal is built from it. We wrote our own, so it is checked against the official examples.

| Test | What it does | Why it matters |
|---|---|---|
| `Sha256.EmptyMessage` | The fingerprint of nothing equals the official value (FIPS 180-4) | Our hash must match the standard exactly, or our seals will not match the Python side's |
| `Sha256.Abc` | The fingerprint of "abc" equals the official value | Same |
| `Sha256.TwoBlockMessage` | A 56-character message, which needs two 64-byte blocks, equals the official value | Checks the tricky part: padding at the block boundary |
| `Sha256.SplitUpdatesMatchOneUpdate` | Hashing 200 bytes one byte at a time gives the same result as hashing them all at once | Data can be fed in pieces (header, then content) |

## HmacSha256: the seal
HMAC mixes a secret key into SHA-256. These are the official examples from RFC 4231.

| Test | What it does | Why it matters |
|---|---|---|
| `HmacSha256.Rfc4231Case1` | Key of 20 × 0x0b, message "Hi There" → official result | Our HMAC matches the standard |
| `HmacSha256.Rfc4231Case2` | Key "Jefe", message "what do ya want for nothing?" → official result | Same, with a short key |
| `HmacSha256.Rfc4231Case6KeyLongerThanBlock` | A 131-byte key (longer than 64) → official result | Long keys are handled (hashed first) |

## SessionKey and DirectionKeys: the keys of each connection
| Test | What it does | Why it matters |
|---|---|---|
| `DirectionKeys.AreHmacOfTheSessionKeyWithG2eAndE2g` | The ground→ESP and ESP→ground keys equal HMAC(session key, "g2e") and HMAC(session key, "e2g") (values from Python), and differ from each other and from the session key | A frame sent back to its own sender fails the seal (#150) |
| `SessionKey.IsHmacOfSessAndBothNonces` | The session key equals HMAC(PSK, "sess" + ground nonce + ESP nonce). Swapping the two nonces gives a different key | Both sides must compute the same key, and the order matters |

## ConstantTime: comparing seals safely
| Test | What it does | Why it matters |
|---|---|---|
| `ConstantTime.EqualAndDifferent` | Equal arrays → equal; arrays differing in the last byte → different; zero length → equal | The comparison takes the same time wherever the difference is, so an attacker cannot guess a seal one byte at a time by timing replies |

## Receiver: the ESP's gatekeeper
The Receiver runs every check on each incoming frame: seal, session, message type, transport, size and SETPOINT mode, then seq. Only frames that pass every check are used.

| Test | What it does | Why it matters |
|---|---|---|
| `Receiver.NothingIsAcceptedBeforeASession` | Before the handshake, even a valid frame is refused (no session) and counted | Nobody can control the drone before connecting properly |
| `Receiver.SessionIdZeroIsReservedForHello` | Opening a session with ID 0 is refused | ID 0 means "no session yet" (HELLO only) |
| `Receiver.AcceptsAValidFrameAndReturnsItsFields` | The reference frame (seq 7) is accepted as a "gap" (1–6 never arrived) and its fields are returned | The normal path works, and lost messages are noticed |
| `Receiver.WrongSessionRejectedEvenWithAValidTag` | A correctly sealed frame for another session (0x12345679) is refused; the seq position does not move | Messages recorded from an old connection cannot be replayed |
| `Receiver.SessionIdZeroOutsideHelloRejected` | A normal frame with session 0 is refused | Same reason as above |
| `Receiver.SeqDuplicateExample` | Sends seq 1, 2, 2, 3: the second 2 is refused (duplicate), the others are accepted | A copied message can never act twice |
| `Receiver.OlderAndTooHighRejected` | After seq 7, seq 3 is refused (old) and seq 2³¹ is refused (too high, new session needed) | A late message never moves the drone back to an old stick position |
| `Receiver.EveryFrameRejectIsCounted` | Five broken frames (short, magic, version, extra byte, bit flip) are each counted under their own reason; then the good frame is still accepted | Each reason has its own counter; only the total reaches the ground (`STATE.rx_rejected`; the rest is open in #149), and a refused frame leaves no trace |
| `Receiver.WrongKeyRejected` | A session opened with a different key refuses our frames (bad seal) | The sender's and the receiver's key for that direction must match, or nothing works |
| `Receiver.NewSessionResetsTheWindowAndEndSessionClosesIt` | A new session starts seq again from 1; after closing the session, everything is refused | Clean start for each connection, and nothing gets through after it closes |
| `Receiver.RejectedFrameNeverReachesTheOutput` | A correctly sealed but old frame (seq 2 after 3) is refused, and the output frame stays empty | The firmware can never use the content of a refused or replayed frame by mistake |
| `Receiver.MalformedPayloadRejectedWithoutUsingTheSeq` | Three sealed frames with seq 5: unknown type 0x99, a 9-byte SETPOINT, a SETPOINT with mode 2. Each is refused and counted under its own reason; seq 1 is still accepted afterwards | A malformed frame changes nothing, not even the message number (REQ-COM-001, REQ-COM-004) |
| `Receiver.TcpSessionStartsAfterHello` | A TCP session starts at seq 1: seq 1 is refused (duplicate), seq 2 accepted with no gap | HELLO already used seq 1, so there is no false gap after the handshake |
| `Receiver.FrameFromTheOtherTransportRejected` | A sealed ARM_CONTROL_REQUEST accepted on TCP is refused when replayed on UDP, and a SETPOINT sent on TCP is refused; neither window moves | Both transports share the direction keys: without this check a captured frame could be replayed on the other one |
| `Receiver.RefusedStartKeepsTheOpenSession` | `start_session` with ID 0 is refused, and the session that was open keeps its ID, window and key | A bad new session never silently drops or changes the current one |
| `Receiver.IsAccepted` | The helper says "yes" only for accepted and accepted-with-gap | The firmware uses this one check to decide |

## Messages: the content of each message type
Each message type is turned into bytes and back (a "round trip"). The bytes are compared with Protocol Spec v0 §3.

| Test | What it does | Why it matters |
|---|---|---|
| `Hello.BytesAndRoundTrip` | HELLO (protocol 1 + 16-byte ground nonce) gives the expected bytes and reads back the same | The handshake starts with this |
| `HelloAck.BytesAndRoundTrip` | HELLO_ACK (session ID, ESP nonce, firmware "v0.1"): the session ID is stored lowest byte first (44 33 22 11) and reads back the same | Little-endian order, as the spec says |
| `U8Payload.RoundTrip` | A one-byte content (BYE reason "replaced" = 3) goes to bytes and back | Used by several short commands |
| `ConfigKv.BytesAndRoundTrip` | Config key 0x80 with value −2 → `80 FE FF FF FF` and back | Negative numbers are stored correctly |
| `ConfigAck.BytesAndRoundTrip` | Reply for Safety key 0x40, result "safety parameter, firmware only" (3), value 250 → `40 03 FA 00 00 00` | The ESP's answer to a config change |
| `Transport.MatchesSpecSection2` | Types 0x01–0x5F are TCP, 0x60–0x9F UDP, anything else neither | Each Receiver knows which types it may accept |
| `Codes.MatchSpecSection31` | Every result, reason, state and event number, and `CONFIG_ACK` = 0x22, equals spec §3.1 | A renumbered code would silently break the ground |
| `Event.EmptyAndFullText` | EVENT code 0x0101 (setpoint stale) with no text, then with the longest text allowed; both read back the same | Variable-length content works at both ends |
| `Event.TooLongRefused` | Text one character too long is refused when building and when reading; a too-small space is refused | No memory overflow from long text |
| `State.BytesAndRoundTrip` | STATE telemetry (link state, last seq, attitude, heading, battery, flow, counters) gives exactly the 28 expected bytes and reads back the same | This is what the ground sees from the drone |
| `Messages.EncodersRefuseANullOutput` | Every message builder, given no output space (null pointer), writes nothing and returns 0 | Same rule as the frame builder and the readers: a bad pointer never crashes the ESP |
| `CheckPayload.EveryTypeHasItsSize` | Every message type is OK at its spec §3 size and refused 1 byte off; EVENT is OK from 2 to 66 bytes | The Receiver refuses a message of the wrong size before using it |
| `CheckPayload.UnknownTypesAndModesRefused` | Types that are not in spec §3 (0x00, 0x08, 0x99, 0xFF…) are refused; SETPOINT mode 0 and 1 pass, 2 and 255 are refused | Only messages we know how to read get through |
| `Messages.WrongSizesRefused` | Every fixed-size reader refuses 1 byte too few and 1 too many; every reader refuses a null pointer; every builder refuses a space 1 byte too small | A wrong size is never half-read |

## SeqWindow: the message number rule
Accept only numbers higher than the last one accepted. Gaps are fine (they are counted).

| Test | What it does | Why it matters |
|---|---|---|
| `SeqWindow.InOrder` | 1, 2, 3 → all accepted, no gaps | The normal case |
| `SeqWindow.DuplicateRejectedAndCounted` | 1, 2, 2, 3 → the second 2 is refused and counted | Wi-Fi sometimes delivers a packet twice |
| `SeqWindow.OlderRejectedAndCounted` | 1, 5, then 3 → 3 is refused (older) | Wi-Fi sometimes delivers packets out of order |
| `SeqWindow.GapAcceptedAndCounted` | 1, 2, 10 → 10 is accepted, 7 missing are counted | A lost packet must not block the next ones |
| `SeqWindow.SeqZeroIsNeverAccepted` | seq 0 is refused | Numbering starts at 1 |
| `SeqWindow.TooHighNeedsNewSession` | 2³¹−1 is accepted; 2³¹ and above are refused | Prevents the number from wrapping around to 0 |
| `SeqWindow.ResetStartsANewSession` | After a reset, the last number is 0 and seq 1 is accepted again | Each session counts from 1 |
| `SeqWindow.ResetToAStartingSeq` | Reset to 1: seq 1 is a duplicate, seq 2 is accepted with no gap | On TCP, HELLO and HELLO_ACK already used seq 1 |

## Setpoint: the stick command
| Test | What it does | Why it matters |
|---|---|---|
| `Setpoint.RoundTrip` | Extreme values (roll −1000, pitch 1000, yaw −1, throttle 1000) go to 10 bytes and back unchanged | Stick values arrive exactly as sent |
| `Setpoint.WrongSizesRefused` | 9 bytes, 11 bytes or a null pointer are refused; a 9-byte space is refused | Only a correct-size setpoint is used |

## SetpointToRc: sticks to flight-controller channels
Converts −1000..1000 to the 1000..2000 µs channel values Betaflight expects. Order: roll, pitch, throttle, yaw, then 4 aux channels.

| Test | What it does | Why it matters |
|---|---|---|
| `SetpointToRc.MapMin` | Roll −1000 → 1000 µs | Full left is the lowest channel value |
| `SetpointToRc.MapCenter` | All 0 → 1500 µs (centre); throttle 0 → 1000 µs | Sticks centred means "hold"; throttle 0 means motors at minimum |
| `SetpointToRc.MapMax` | Roll 1000 → 2000 µs | Full right is the highest channel value |
| `SetpointToRc.ClampOver` | Roll 1500 → 2000, pitch −32768 → 1000, yaw 32767 → 2000; throttle 500 → 1500 | Out-of-range values never reach the FC (safety) |
| `SetpointToRc.ThrottleMap` | Throttle 0 → 1000, 1000 → 2000, −50 → 1000, 1200 → 2000 | Throttle is clamped too |
| `SetpointToRc.AuxAlwaysNeutral` | Channels 5–8 are always 1500, whatever the sticks | The ground can never flip the arm or mode switches (gestures never arm) |
| `SetpointToRc.AxisOrderIsAetr` | Checks each value lands on the right channel: roll, pitch, throttle, yaw | Swapped channels would make the drone fly the wrong way |

## Golden vectors: examples shared with the Python twin
Fixed examples in `drone/tests/vectors/*.json`, made by `generate.py` in that folder. The Python ground code (#100) checks the same files. If both pass, the PC and the drone agree byte for byte. Each vector's "does" line is the `description` in its JSON. The rows named after a file give the "why" for that file's vectors.

| Test | What it does | Why it matters |
|---|---|---|
| `Vectors.FilesLoadedAndNamesUnique` | Loads every vector file: at least 60 vectors, no name used twice | A missing file or a reused name would silently skip checks |
| `1_valid_frames` | One message of each type: fields → bytes → fields | Both sides build and read every message the same way |
| `2_hmac` | Seals made with the PSK and with each direction key; a reflected frame; one changed bit | The seal is computed over the right bytes with the right key, and a reflection is refused |
| `3_header` | Broken envelopes (magic, version, length, truncated), then sealed frames with an unknown type or a wrong size | Garbage is refused before it is read, and a malformed frame does not use up its seq |
| `4_session` | Frames for another session, or session 0 | Old or foreign connections cannot be replayed |
| `5_sequence` | Several frames in a row, the result after each one | A copied or late message never acts |
| `6_clamps` | Setpoints to the 8 RC channels; a SETPOINT with mode 2 is refused and the same seq is then still accepted | The FC gets the right channels and never a value out of range, and an unknown mode never reaches it |
| `7_config` | One CONFIG_ACK per result 0–5 (spec §3.1) | Both sides agree on every config answer |
| `8_reserved_and_transports` | Reserved flags ignored, HELLO_ACK tagged with the PSK, TCP and UDP seq counted apart, a frame on the wrong transport refused | The 2026-11-05 decisions hold on both sides |

## Version: protocol version
| Test | What it does | Why it matters |
|---|---|---|
| `Version.MatchesProtocolSpecV0HeaderVersion` | The library reports version 1, the value in the frame header | The C++ and Python sides must agree on the version |
