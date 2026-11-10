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
| `SecureZero.ClearsEveryByteAndNothingElse` | Zeroes exactly the bytes asked for, including a whole key | The session key can really be wiped: a plain memset can be dropped by the compiler |
| `DirectionKeys.DeriveKeysEqualsTheTwoSteps` | `derive_keys()` gives the same two keys as the session key then the direction keys | The one public function gives the right keys without handing out the session key (#152) |
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
| `Receiver.FrameSealedForTheOtherDirectionRejected` | ESP and ground Receivers opened with their direction keys accept the real traffic; each side's own frame sent back to it (seq 1000) is a bad tag and moves nothing | A reflection never reaches either side (#150) |
| `Receiver.OwnFramesRejectedEvenWithTheSameKey` | With the same key everywhere, the ESP refuses a STATE and an ARM_CONTROL_ACK, the ground a SETPOINT and an ARM_CONTROL_REQUEST (`wrong_direction`), no window moves; BYE is accepted by both; the real traffic still goes through | Even if both ends got the keys wrong, a node's own frame is never accepted (#152) |
| `Receiver.KeepsItsRoleAndTransport` | A Receiver created for the ground on TCP reports exactly that | The role and transport are fixed for its whole life |
| `Handshake.EachSideAcceptsOnlyItsOwnHandshakeFrame` | The ESP accepts HELLO, the ground HELLO_ACK; each refuses the other's and a PSK-tagged SETPOINT; wrong session ID, seq 2, short payload, bad tag and a cut frame refused; the output is written only on accept | Before a session only the right handshake frame gets through (#152) |
| `Receiver.ResultNumbersMatchThePythonTwin` | Every reject reason has the same number as in the Python twin (0–16), and the handshake results too | A number in a log means the same reason on both sides; new values are only appended |
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
| `Role.ReceivesOnlyWhatTheOtherSideSends` | The ESP receives SETPOINT and the requests, the ground STATE, the ACKs and EVENT, both BYE; nobody HELLO/HELLO_ACK or unknown types in a session | Each Receiver knows which types it may accept (spec §2) |
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
| `Vectors.FilesLoadedAndNamesUnique` | Loads every vector file: at least 69 vectors, no name used twice | A missing file or a reused name would silently skip checks |
| `1_valid_frames` | One message of each type: fields → bytes → fields | Both sides build and read every message the same way |
| `2_hmac` | Seals made with the PSK and with each direction key; a reflected frame; one changed bit | The seal is computed over the right bytes with the right key, and a reflection is refused |
| `3_header` | Broken envelopes (magic, version, length, truncated), then sealed frames with an unknown type or a wrong size | Garbage is refused before it is read, and a malformed frame does not use up its seq |
| `4_session` | Frames for another session, or session 0 | Old or foreign connections cannot be replayed |
| `5_sequence` | Several frames in a row, the result after each one | A copied or late message never acts |
| `6_clamps` | Setpoints to the 8 RC channels; a SETPOINT with mode 2 is refused and the same seq is then still accepted | The FC gets the right channels and never a value out of range, and an unknown mode never reaches it |
| `7_config` | One CONFIG_ACK per result 0–5 (spec §3.1) | Both sides agree on every config answer |
| `8_reserved_and_transports` | Reserved flags ignored, HELLO_ACK tagged with the PSK, TCP and UDP seq counted apart, a frame on the wrong transport or from the wrong side refused | The 2026-11-05 decisions hold on both sides |
| `9_handshake` | HELLO accepted only by the ESP, HELLO_ACK only by the ground; reflected ones, other PSK-tagged types, a wrong session ID or seq ≠ 1 refused | Before a session nothing but the right handshake frame gets through (#152) |

## Msp: talking to the flight controller (#101)
The ESP speaks MSP v1 to the FC over the UART (ADR-009). It may only send the commands on the allow-list of the MSP Cheat Sheet.

| Test | What it does | Why it matters |
|---|---|---|
| `Msp.KnownRequestBytes` | MSP_API_VERSION and MSP_STATUS requests are exactly `$M< 00 01 01` and `$M< 00 65 65` | The bytes match what Betaflight expects |
| `Msp.AllowListRefusesEverythingElse` | All 256 command numbers: only 1, 101, 105, 108 and 110 can be built as requests, and 200 only through its typed encoder; MSP_SET_MOTOR, EEPROM_WRITE, REBOOT and ARMING_DISABLED cannot | A forbidden command can never reach the FC |
| `Msp.EncodeRefusesBadArguments` | Null buffers, a too-small buffer, a too-long payload are refused | A bad call writes nothing |
| `Msp.SetRawRcOnlyThroughTheTypedEncoder` | `encode_request` refuses MSP_SET_RAW_RC with every payload length 0–64; only `encode_set_raw_rc` builds it, always with 8 channels | The one write command on the allow-list cannot be built with an arbitrary payload |
| `Msp.SetRawRcBytes` | 8 channels become a 16-byte little-endian payload with the right checksum | The RC frame #102 will send is right |

## MspParser and MspDecode: reading the FC's replies
| Test | What it does | Why it matters |
|---|---|---|
| `MspParser.ReadsRepliesAndSkipsNoise` | Noise and a false start, then an MSP_ATTITUDE reply: one reply, roll −10.0°, pitch 30.0°, heading 359° | Boot noise on the UART does not confuse the reader |
| `MspParser.BadChecksumTooLongAndRequestsAreDropped` | A wrong checksum and a 100-byte reply full of `$` are dropped, the next reply is read; an echoed request is not a reply | A corrupted or oversized frame never becomes data |
| `MspParser.ErrorReplyAndEmptyReply` | An `!` error reply and an empty reply are reported as such and never decoded as values | The FC refusing a command is not read as numbers |
| `MspParser.RandomBytesNeverCrashAndAlwaysResync` | 200,000 random bytes, then a valid reply: the reply is read | The reader always recovers |
| `MspDecode.StatusArmedAndArmingFlags` | MSP_STATUS laid out as Betaflight 2026.6.2 writes it: armed bit and arming-disable flags, with 0 to 3 extra mode bytes; a cut reply is refused | The ESP knows if the drone is armed (failsafe, ADR-024 gate) |
| `MspDecode.AnalogTakesTheCentivoltField` | MSP_ANALOG gives 1110 = 11.10 V from its last field; an old 7-byte reply is refused | The battery voltage has the unit STATE needs |

## FcLink: the scheduler that owns the FC UART (#101)
| Test | What it does | Why it matters |
|---|---|---|
| `FcLink.PollsAtTheAdr009Rates` | One simulated second: 25 ATTITUDE, 5 STATUS, 5 ANALOG requests, every one answered | The rates of ADR-009 |
| `FcLink.OnlyPollRequestsEverLeave` | Five simulated seconds: only the three poll commands are sent | The gateway alone never sends RC |
| `FcLink.OneRequestAtATimeAndTimeouts` | Nothing else is sent while a reply is awaited; after 50 ms it is a timeout, then 50 ms of drain where nothing is sent and a late reply is dropped | Requests never collide, and a late reply is never taken as the answer to a newer request |
| `FcLink.ArmedStateAndFreshness` | The armed flag is read, and the status is fresh for 500 ms only | The ADR-024 gate needs a fresh "disarmed" |
| `FcLink.ErrorAndBadRepliesCounted` | Error replies, bad checksums and undecodable replies are counted, never stored | Bad data never reaches the telemetry |
| `FcLink.SurvivesTheMillisWrap` | Polling keeps going across the 49-day `millis()` wrap | No stall after a long uptime |
| `FcLink.OldValuesAreForgotten` | The FC goes silent: after 10 s every value is forgotten, and an old status never looks fresh again, even across the `millis()` wrap | An old "disarmed" can never open the ADR-024 OTA gate |
| `FcLink.PollRefusesASmallBuffer` | A buffer too small for a request: nothing is sent or counted | A bad call writes nothing |
| `FcLink.ReadyOnlyAfterTheFcAnswersApiVersion` | `fc_ready()` is false until MSP_API_VERSION is answered (polled at 1 Hz, version stored), and false again 10 s after the FC goes silent | The ESP leaves BOOT only when the FC link works (#102) |
| `FcLink.ErrorReplyToApiVersionIsNotReady` | An error reply to MSP_API_VERSION never makes the link ready | A refusing FC is not a working link |
| `FcLink.RcGoesFirstAndIsAcknowledged` | `set_rc()`: the next request is MSP_SET_RAW_RC with the 8 channels in order, its empty reply is counted, then the polls go on | The control output always goes first |
| `FcLink.OnlyTheLatestRcIsSent` | Two RC sets while a poll is in flight: the newer one is sent, the older one counted as replaced | Old sticks never reach the FC after newer ones |
| `FcLink.StopRcSendsNothing` | `stop_rc()` after `set_rc()`: no RC frame | The ADR-004 "stop" really stops |
| `FcLink.RcAt50HzLeavesTheAdr009Polls` | One simulated second with RC at 50 Hz and an FC that answers at once: 50 RC frames, 25 ATTITUDE, no timeouts | RC and telemetry fit on one UART (the real budget is spike #123) |
| `FcLink.ASilentFcDoesNotStarveThePolls` | The FC stops answering for 3 s with RC at 50 Hz: polls still go out between the RC timeouts, and when the FC answers again its values come back | A silent FC is seen coming back; RC timeouts never lock out telemetry (safety review of #102 PR 2) |

## Link: the ESP link state machine (#102)

`sd::Link` with a fake ground that does the real handshake. Times are in ms; each test checks one rule of the ESP Link State Machine note.

| Test | What it does | Why it matters |
|---|---|---|
| `LinkTest.BootSendsNothingAndDropsEverything` | Before `boot_done()`: nothing goes to the FC, a HELLO closes its connection, UDP is dropped | Safe at power-up and after a watchdog reset (REQ-SAF-016) |
| `LinkTest.NoSessionSendsNeutralFrames` | After boot with no session: neutral sticks (throttle 1000) and no STATE | An early override switch-on never sees empty channels |
| `LinkTest.HelloInNoSessionOpensTheSessionAtOnce` | A valid HELLO with no session answers HELLO_ACK on the same connection and opens SESSION_IDLE; the ground's keys work | Nothing to protect yet, so no pending step |
| `LinkTest.BadHelloIsCountedAndKeepsOrClosesTheConnection` | A bad tag or unknown `proto` is counted and the connection stays; a bad magic closes it | Spec §4 rule 5: close only when the framing is broken |
| `LinkTest.ABadHelloDoesNotUseTheRateLimitWindow` | A bad HELLO and an unknown-proto HELLO, then a valid one 200 ms later: accepted | Garbage cannot block a real ground from connecting |
| `LinkTest.BadFramesOnTheSessionConnection` | On the session's connection, a bad tag is dropped and the connection stays; a bad version closes it; no failsafe | Spec §4 rule 5 for session frames |
| `LinkTest.NonHelloOnAConnectionWithoutSessionClosesIt` | A valid session frame sent on another connection closes that connection | A connection with no session carries HELLO only (ADR-005) |
| `LinkTest.UdpWithoutASessionIsDroppedAndCounted` | UDP with no session open: dropped as `no session` and counted | Nothing reaches control without a session |
| `LinkTest.SessionIdsComeFromTheRngAndAreNeverZero` | An RNG that gives only zeros: the HELLO is refused, no session opens | `session_id` 0 is reserved for HELLO |
| `LinkTest.ArmWithTheWrongMaskIsRefused` | ARM with mask 15 on a drone set to 8: result 2, state unchanged | The ground must not fly with the wrong override mask |
| `LinkTest.ArmOkEntersStreamingAndForwardsFreshSetpoints` | ARM OK → STREAMING; a mode-1 setpoint becomes RC values, a mode-0 one neutral | The basic control path |
| `LinkTest.SetpointsSentBeforeArmNeverReachTheFc` | Roll 800 / throttle 700 sent before ARM: neutral right after ARM; the next setpoint is used | Sticks sent while control was off are never applied when it turns on |
| `LinkTest.ArmIsRefusedOutsideSessionIdle` | ARM in STREAMING or LINK_FAILSAFE: result 1 (wrong state) | Leaving the failsafe needs RESUME, not ARM |
| `LinkTest.ArmWithoutSetpointsFailsAfterTLoss` | ARM OK and no setpoint: still STREAMING at 249 ms, LINK_FAILSAFE at 250 ms | ARM alone never keeps control alive |
| `LinkTest.StaleSetpointBecomesNeutralAfterTFresh` | A setpoint is used until 149 ms, neutral at 150 ms, one stale EVENT; the next setpoint is used at once | `T_fresh` of spec §7.2 |
| `LinkTest.LossAfterTLossEntersFailsafe` | No setpoint: STREAMING at 249 ms, LINK_FAILSAFE at 250 ms, with an EVENT | `T_loss` of ADR-004 |
| `LinkTest.ALateSetpointDoesNotRescueStreaming` | A setpoint that arrives 300 ms after the last one: the failsafe is entered first | Expired timers are applied before any input |
| `LinkTest.RejectedFramesDoNotResetTheLossTimer` | A replay, a bad tag and a stranger's frame during the gap: the failsafe still comes at 250 ms | REQ-SAF-011: only accepted frames count |
| `LinkTest.FailsafeOn2026SendsNeutralForOneSecondThenStops` | In LINK_FAILSAFE: neutral for 1 s, then nothing | ADR-004 on Betaflight 2026.6: the FC falls back to the radio sticks |
| `LinkTest.FailsafeOn451SendsNeutralForever` | With the 4.5.1 setting: neutral frames forever | ADR-004 on 4.5.1, where overrides are held |
| `LinkTest.FailsafeStopSurvivesAClockWrap` | Once the output has stopped, a 2^32 ms clock wrap does not restart the neutral | No surprise output after a long uptime |
| `LinkTest.PacketsComingBackDoNotResumeControl` | Mode-1 setpoints arrive again for 1 s after the loss: still LINK_FAILSAFE, only the ADR-004 output | Control never comes back by itself |
| `LinkTest.ResumeNeedsFiveHundredMsOfNeutralStream` | RESUME after 460 ms of neutral: result 3; after 500 ms: OK, STREAMING, EVENT | The resume rule of ADR-004 |
| `LinkTest.AGapOrANonNeutralSetpointRestartsTheNeutralStream` | A mode-1 setpoint or a 150 ms gap restarts the 500 ms | The neutral stream must be continuous |
| `LinkTest.ResumeNeedsAFreshSetpoint` | RESUME when the last neutral setpoint is 150 ms old: result 3 | The link must be alive right now |
| `LinkTest.ResumeOutsideFailsafeIsWrongState` | RESUME in SESSION_IDLE: result 1 | RESUME only leaves LINK_FAILSAFE |
| `LinkTest.NewHelloDuringStreamingOpensAPendingSessionOnly` | A second HELLO while streaming: HELLO_ACK, pending session, the old session keeps flying | #156: a HELLO never ends a live session |
| `LinkTest.ReplayedHelloNeverEndsTheLiveSession` | A recorded HELLO replayed 5 times on another socket: never confirmed, expires, its connection closes; streaming goes on | A replay without the PSK can do nothing to the live session |
| `LinkTest.PendingSessionExpiresAfterTwoSeconds` | A pending session is still there at 1,999 ms and gone at 2,000 ms; its keys no longer work | ADR-005: pending for at most 2 s |
| `LinkTest.FirstValidFrameUnderTheNewKeysConfirmsTheSwitch` | The new session's first setpoint switches: BYE reason 3 to the old session, its connection closed, old frames refused | How a real reconnect takes over |
| `LinkTest.SwitchFromStreamingKeepsTheFailsafeOutputUntilArm` | A switch while streaming: the ADR-004 output until the new session's ARM OK | No throttle cut at a session switch (§1.1 of the state machine note) |
| `LinkTest.SwitchFromFailsafeKeepsItsTimeline` | A switch in LINK_FAILSAFE: the 1 s of neutral is not restarted, and RESUME is refused in the new session | A new session is not a resume (REQ-COM-020) |
| `LinkTest.SwitchFromSessionIdleSendsNeutralAtOnce` | A switch when control was off: plain neutral frames | Nothing to protect when control was off |
| `LinkTest.RehandshakeOnTheSameConnectionKeepsIt` | HELLO on the session's own connection: BYE 3 goes out on it, and it stays open for the new session | ADR-005 entry 2026-11-06 |
| `LinkTest.OldSessionFramesStillWorkOnASharedConnection` | While pending on the same connection, the old session's frames are still answered | The old session runs until the switch |
| `LinkTest.HelloIsRateLimitedAndARefusedOneDoesNotRestartTheWindow` | A HELLO 300 ms after the last one is refused; one at 500 ms is accepted | At most one HELLO per 500 ms, and a refused one does not extend the wait |
| `LinkTest.ALaterHelloReplacesThePendingOne` | A third HELLO replaces the pending one: its connection closes and its keys no longer work | Only the newest pending session can take over |
| `LinkTest.PendingSessionIsDroppedWhenItsConnectionCloses` | The pending session's connection closes: the pending session is gone | It could not answer anyway |
| `LinkTest.ForeignFrameOnThePendingConnectionIsRejected` | The current session's frame sent on the pending connection: refused, nothing switches | Frames count only on their own connection |
| `LinkTest.TcpDropWhileStreamingChangesNothingWhileUdpFlows` | TCP closes while streaming: still STREAMING for 2 s of setpoints, STATE still sent | ADR-005: TCP never decides link loss |
| `LinkTest.ReconnectAfterATcpDropNeverInterruptsControlOutput` | TCP drops, a new HELLO, then ARM as the first new-key frame: STREAMING continues under the new session | The reconnect bench case of #102 |
| `LinkTest.SessionIdleWithNoTcpAndNoUdpEndsTheSession` | SESSION_IDLE, TCP closed, no UDP: still open at 249 ms, NO_SESSION at 250 ms | "TCP closed and no UDP" of the state machine |
| `LinkTest.SessionIdleWithNoTcpButUdpKeepsTheSession` | SESSION_IDLE, TCP closed, setpoints keep coming: the session stays | UDP alone keeps a session alive |
| `LinkTest.ByeInSessionIdleEndsTheSession` | BYE in SESSION_IDLE: NO_SESSION, no reply, connection closed, neutral frames | BYE ends a session |
| `LinkTest.ByeInFailsafeEndsTheSessionAndKeepsItsTimeline` | BYE in LINK_FAILSAFE: NO_SESSION, the ADR-004 output keeps its 1 s timeline | Ending the session never restarts the output |
| `LinkTest.ByeAsTheFirstFrameOfAPendingSession` | The pending session's first frame is a BYE: switch (BYE 3 to the old one), then NO_SESSION with the ADR-004 output | Edge case of the switch, covered |
| `LinkTest.APendingSessionOutlivesAByeOfTheCurrentOne` | The current session says BYE while another is pending: the pending one can still take over, with no BYE 3 | No old session left to tell |
| `LinkTest.ByeOfTheOldSessionKeepsASharedConnectionOpen` | A pending session on the current session's connection, then BYE of the old session: the connection stays open and the pending session takes over on it | A BYE never closes the connection another session needs |
| `LinkTest.ReplacingASharedPendingSessionKeepsTheConnection` | A pending session on the current connection is replaced by a HELLO on another connection: the current connection stays open | Dropping a pending session never cuts the live one |
| `LinkTest.NewSessionIsToldTheFailsafeOutputStillRuns` | A new session opened after a BYE from control, or confirmed by a switch from STREAMING, gets EVENT 0x0104; a session with no ADR-004 output running gets none | The ground never shows SESSION_IDLE as if the output were neutral (safety pilot, review of #170) |
| `LinkTest.ByeWhileStreamingKeepsTheFailsafeOutput` | BYE while streaming: NO_SESSION with the ADR-004 output; a new session does not clear it, only its ARM | A BYE is never an instant neutral |
| `LinkTest.ConfigGetAnswersKnownAndUnknownKeys` | CONFIG_GET on every spec §7.3 key gives its value; unknown keys give result 1 and value 0 | The key table of the spec |
| `LinkTest.ConfigSetChecksStateFirstThenTheKey` | CONFIG_SET while streaming: result 5 for any key; then unknown 1, read-only 2, safety 3, out of range 4 (never clamped), OK | The check order of spec §3.1 |
| `LinkTest.StateCarriesTheLinkFieldsAndTheFcView` | STATE has the link state, last control seq, rejected count and the FC values; seq counts up | What the ground sees |
| `LinkTest.EspFramesAreAcceptedByTheGroundReceiver` | Every TCP frame the ESP sent passes a ground-side Receiver | Both ends agree on keys, seq and direction |

## OtaLock / OtaGate: the ADR-024 OTA gate and upload lock (#102)

A fake FC answers like Betaflight: MSP_STATUS, and MSP_SET_ARMING_DISABLED setting or clearing ARMING_DISABLED_MSP.

| Test | What it does | Why it matters |
|---|---|---|
| `OtaLock.ArmingDisabledHasItsOwnEncoderOnly` | MSP 99 is not on the allow-list (the generic encoder refuses it with any payload); only its own encoder builds it, byte for byte | Nothing from the network can ever build the command that disarms the FC |
| `OtaLock.LocksADisarmedFcAndReadsTheLockBack` | Disarmed FC: buffered input discarded, then STATUS, SET_ARMING_DISABLED 1, STATUS, in that order; the lock bit is read back | ADR-024 lock steps 1 and 2 |
| `OtaLock.NeverSendsTheLockToAnArmedFc` | Armed FC: result "armed" and value 1 is never sent | Betaflight disarms an armed FC on value 1 (ADR-024: never without step 1) |
| `OtaLock.NeverSendsTheLockWithoutAFreshStatus` | A buffered old "disarmed" reply, then a silent FC: result "no reply", value 1 never sent, gives up within the timeout | Only a fresh status counts; a dead FC never gets the lock |
| `OtaLock.ALateOldReplyIsNeverTakenAsFresh` | An old "disarmed" reply to an earlier poll arrives 20 ms after the discard while the FC is armed: the lock waits for 60 ms of quiet, asks again, sees "armed", never sends value 1 | Only the reply to the lock's own request counts (ADR-024 step 1; review of #102 part 3) |
| `OtaLock.AnFcThatNeverGoesQuietGetsNoLock` | The UART never goes quiet: no request at all, "no reply" within 500 ms | A babbling line never leads to a lock |
| `OtaLock.FailsWhenTheFcDoesNotConfirmTheLock` | An error reply to the lock, or an ack without the flag: result "not locked" | The upload is aborted unless the lock is really set |
| `OtaLock.ReleaseSendsValueZero` | Release sends value 0 (no status check needed) and clears the flag; a silent FC returns false | Value 0 is harmless at any time (ADR-024) |
| `OtaGate.OpenOnlyWithNoSessionAndAFreshDisarmedStatus` | Closed in BOOT; open in NO_SESSION with a disarmed status younger than 500 ms; closed at 500 ms | ADR-024 D2 |
| `OtaGate.ClosedWhenArmedOrUnknown` | No status yet, or an armed status: closed | An unknown arm state refuses OTA |
| `OtaGate.ClosedWhileASessionIsOpen` | SESSION_IDLE: closed; after the session ends (NO_SESSION): open again | No update while a ground session is open |

## Version: protocol version
| Test | What it does | Why it matters |
|---|---|---|
| `Version.MatchesProtocolSpecV0HeaderVersion` | The library reports version 1, the value in the frame header | The C++ and Python sides must agree on the version |
