#include <gtest/gtest.h>

#include <cstring>
#include <string>

#include "sd/hmac.hpp"

namespace {

std::string hex(const std::uint8_t *p, std::size_t n) {
  static const char kDigits[] = "0123456789abcdef";
  std::string s;
  for (std::size_t i = 0; i < n; ++i) {
    s += kDigits[p[i] >> 4];
    s += kDigits[p[i] & 0x0f];
  }
  return s;
}

std::string sha256_hex(const std::string &msg) {
  sd::Sha256 h;
  h.update(reinterpret_cast<const std::uint8_t *>(msg.data()), msg.size());
  std::uint8_t out[sd::kSha256Size];
  h.finish(out);
  return hex(out, sizeof(out));
}

std::string hmac_hex(const std::string &key, const std::string &msg) {
  std::uint8_t out[sd::kSha256Size];
  sd::hmac_sha256(
      reinterpret_cast<const std::uint8_t *>(key.data()), key.size(),
      reinterpret_cast<const std::uint8_t *>(msg.data()), msg.size(), out);
  return hex(out, sizeof(out));
}

} // namespace

// FIPS 180-4 examples.
TEST(Sha256, EmptyMessage) {
  EXPECT_EQ(sha256_hex(""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
}

TEST(Sha256, Abc) {
  EXPECT_EQ(sha256_hex("abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
}

TEST(Sha256, TwoBlockMessage) {
  EXPECT_EQ(
      sha256_hex("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
      "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
}

TEST(Sha256, SplitUpdatesMatchOneUpdate) {
  const std::string msg(200, 'x');
  sd::Sha256 h;
  for (char c : msg) {
    const auto b = static_cast<std::uint8_t>(c);
    h.update(&b, 1);
  }
  std::uint8_t out[sd::kSha256Size];
  h.finish(out);
  EXPECT_EQ(hex(out, sizeof(out)), sha256_hex(msg));
}

// RFC 4231 test cases 1, 2 and 6.
TEST(HmacSha256, Rfc4231Case1) {
  EXPECT_EQ(hmac_hex(std::string(20, '\x0b'), "Hi There"),
            "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7");
}

TEST(HmacSha256, Rfc4231Case2) {
  EXPECT_EQ(hmac_hex("Jefe", "what do ya want for nothing?"),
            "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843");
}

TEST(HmacSha256, Rfc4231Case6KeyLongerThanBlock) {
  EXPECT_EQ(hmac_hex(std::string(131, '\xaa'),
                     "Test Using Larger Than Block-Size Key - Hash Key First"),
            "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54");
}

TEST(SessionKey, IsHmacOfSessAndBothNonces) {
  sd::Key psk{};
  for (std::size_t i = 0; i < sd::kKeySize; ++i) {
    psk.bytes[i] = static_cast<std::uint8_t>(i);
  }
  std::uint8_t ground[sd::kNonceSize];
  std::uint8_t esp[sd::kNonceSize];
  std::memset(ground, 0x11, sizeof(ground));
  std::memset(esp, 0x22, sizeof(esp));

  std::uint8_t msg[4 + 2 * sd::kNonceSize];
  std::memcpy(msg, "sess", 4);
  std::memcpy(msg + 4, ground, sd::kNonceSize);
  std::memcpy(msg + 4 + sd::kNonceSize, esp, sd::kNonceSize);
  std::uint8_t expected[sd::kSha256Size];
  sd::hmac_sha256(psk.bytes, sd::kKeySize, msg, sizeof(msg), expected);

  const sd::Key session = sd::detail::derive_session_key(psk, ground, esp);
  EXPECT_EQ(hex(session.bytes, sd::kKeySize), hex(expected, sd::kSha256Size));

  // Swapping the nonces gives a different key.
  const sd::Key swapped = sd::detail::derive_session_key(psk, esp, ground);
  EXPECT_NE(hex(swapped.bytes, sd::kKeySize), hex(session.bytes, sd::kKeySize));
}

TEST(ConstantTime, EqualAndDifferent) {
  const std::uint8_t a[4] = {1, 2, 3, 4};
  const std::uint8_t b[4] = {1, 2, 3, 4};
  const std::uint8_t c[4] = {1, 2, 3, 5};
  EXPECT_TRUE(sd::equal_constant_time(a, b, 4));
  EXPECT_FALSE(sd::equal_constant_time(a, c, 4));
  EXPECT_TRUE(sd::equal_constant_time(a, c, 0));
}

// ADR-002 entry of 2026-11-05 (#150). Expected values from Python's hmac
// module: PSK 00..1F, ground nonce 11 x 16, ESP nonce 22 x 16.
TEST(DirectionKeys, AreHmacOfTheSessionKeyWithG2eAndE2g) {
  sd::Key psk{};
  for (std::size_t i = 0; i < sd::kKeySize; ++i) {
    psk.bytes[i] = static_cast<std::uint8_t>(i);
  }
  std::uint8_t ground[sd::kNonceSize];
  std::uint8_t esp[sd::kNonceSize];
  std::memset(ground, 0x11, sizeof(ground));
  std::memset(esp, 0x22, sizeof(esp));
  const sd::Key session = sd::detail::derive_session_key(psk, ground, esp);
  const sd::DirectionKeys keys = sd::detail::derive_direction_keys(session);

  EXPECT_EQ(hex(keys.ground_to_esp.bytes, sd::kKeySize),
            "bb0b20baf093c79ac77f89eb087d0e68a35bb31cf90f34a09bb2d41837b9e117");
  EXPECT_EQ(hex(keys.esp_to_ground.bytes, sd::kKeySize),
            "7353101a8458bfe9911c7269d2ec7aa4f94d13c0b2cfad27bd454e078f96b33d");
  // Three different keys: a frame sealed for one direction never passes the
  // other direction's check.
  EXPECT_NE(hex(keys.ground_to_esp.bytes, sd::kKeySize),
            hex(keys.esp_to_ground.bytes, sd::kKeySize));
  EXPECT_NE(hex(keys.ground_to_esp.bytes, sd::kKeySize),
            hex(session.bytes, sd::kKeySize));
  EXPECT_NE(hex(keys.esp_to_ground.bytes, sd::kKeySize),
            hex(session.bytes, sd::kKeySize));
}

TEST(SecureZero, ClearsEveryByteAndNothingElse) {
  std::uint8_t buf[40];
  std::memset(buf, 0xAB, sizeof(buf));
  sd::secure_zero(buf + 4, 32);
  for (std::size_t i = 0; i < sizeof(buf); ++i) {
    EXPECT_EQ(buf[i], (i >= 4 && i < 36) ? 0 : 0xAB) << i;
  }
  sd::Key key{};
  std::memset(key.bytes, 0x5A, sd::kKeySize);
  sd::secure_zero(&key, sizeof(key));
  EXPECT_EQ(hex(key.bytes, sd::kKeySize), std::string(64, '0'));
}

// #152: derive_keys() gives the same two keys as the two steps, without
// handing out the session key.
TEST(DirectionKeys, DeriveKeysEqualsTheTwoSteps) {
  sd::Key psk{};
  for (std::size_t i = 0; i < sd::kKeySize; ++i) {
    psk.bytes[i] = static_cast<std::uint8_t>(i);
  }
  std::uint8_t ground[sd::kNonceSize];
  std::uint8_t esp[sd::kNonceSize];
  std::memset(ground, 0x11, sizeof(ground));
  std::memset(esp, 0x22, sizeof(esp));
  const sd::DirectionKeys keys = sd::derive_keys(psk, ground, esp);
  const sd::DirectionKeys steps = sd::detail::derive_direction_keys(
      sd::detail::derive_session_key(psk, ground, esp));
  EXPECT_EQ(hex(keys.ground_to_esp.bytes, sd::kKeySize),
            hex(steps.ground_to_esp.bytes, sd::kKeySize));
  EXPECT_EQ(hex(keys.esp_to_ground.bytes, sd::kKeySize),
            "7353101a8458bfe9911c7269d2ec7aa4f94d13c0b2cfad27bd454e078f96b33d");
}
