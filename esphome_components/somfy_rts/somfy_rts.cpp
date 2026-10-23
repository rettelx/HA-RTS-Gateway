#include "somfy_rts.h"

#include <cinttypes>

#include "esphome/core/log.h"

namespace esphome::somfy_rts {

static const char *const TAG = "somfy_rts";

static bool near(int32_t duration, uint32_t expected, uint32_t tolerance_pct) {
  // Spaces are negative in ESPHome captures: fold them before comparing.
  const uint32_t abs_duration =
      duration < 0 ? static_cast<uint32_t>(-duration) : static_cast<uint32_t>(duration);
  const uint32_t lower = expected * (100 - tolerance_pct) / 100;
  const uint32_t upper = expected * (100 + tolerance_pct) / 100;
  return abs_duration >= lower && abs_duration <= upper;
}

/// Map the hardware sync run count to the frame bit length.
///
/// 56-bit frames: 4 runs (first frame, 2 pulses) or 14 runs (repeats,
/// 7 pulses). 80-bit frames: 12/13 runs (step-wheel remotes, 6 pulses) or
/// more than 17 runs.
static uint32_t bit_length_for_hw_runs(uint32_t runs) {
  if (runs <= 7) return FRAME_BITS;
  if (runs >= 12 && runs <= 13) return FRAME_BITS_80;
  if (runs > 17) return FRAME_BITS_80;
  return FRAME_BITS;
}

/// 80-bit extension checksum.
static uint8_t checksum80(uint8_t b7, uint8_t b8, uint8_t b9) {
  uint8_t cs = (((b7 & 0xF0) >> 4) ^ ((b8 & 0xF0) >> 4)) ^ ((b9 & 0xF0) >> 4);
  cs ^= (b7 & 0x0F) ^ (b8 & 0x0F);
  return cs & 0x0F;
}

void SomfyRTSComponent::setup() {
  // The listener slot is registered from to_code() via remote_base.attach_receiver;
  // ESPHome 2026.10 no longer allows runtime listener registration from C++.
  if (this->receiver_ == nullptr) {
    ESP_LOGW(TAG, "No remote receiver configured");
  }
}

void SomfyRTSComponent::dump_config() {
  ESP_LOGCONFIG(TAG, "Somfy RTS decoder:");
  ESP_LOGCONFIG(TAG, "  Receiver: %s", this->receiver_ != nullptr ? "configured" : "none");
  ESP_LOGCONFIG(TAG, "  Frame lengths: 56 and 80 bits");
  ESP_LOGCONFIG(TAG, "  Trailing half-bit completion: enabled");
}

bool SomfyRTSComponent::on_receive(remote_base::RemoteReceiveData data) {
  const int32_t count = data.size();
  if (count < 8) {
    return false;
  }

  // The capture alternates mark (positive) and space (negative) runs.
  // Locate the software sync: a ~4550 us mark preceded by hardware sync
  // runs (~2416 us each).
  for (int32_t i = 1; i + 1 < count; i++) {
    const int32_t sync_mark = data.peek(i);
    const int32_t hw_space = data.peek(i - 1);
    if (sync_mark <= 0 || hw_space >= 0) {
      continue;
    }
    if (!near(sync_mark, SW_SYNC_MARK_US, TOLERANCE_PCT)) {
      continue;
    }
    if (!near(hw_space, HW_SYNC_US, TOLERANCE_PCT)) {
      continue;
    }

    // Count the hardware sync runs leading up to the software sync.
    uint32_t hw_runs = 0;
    for (int32_t j = i - 1; j >= 0; j--) {
      const int32_t run = data.peek(j);
      if (run == 0 || !near(run < 0 ? -run : run, HW_SYNC_US, TOLERANCE_PCT)) {
        break;
      }
      hw_runs++;
    }
    if (hw_runs < MIN_HW_RUNS) {
      continue;
    }

    if (this->decode_frame_(data, i, hw_runs)) {
      return true;
    }
  }
  return false;
}

bool SomfyRTSComponent::decode_frame_(const remote_base::RemoteReceiveData &data, uint32_t sync,
                                       uint32_t hw_runs) {
  const uint32_t bits = bit_length_for_hw_runs(hw_runs);
  const uint32_t units_needed = bits * 2 + 1;
  const int32_t count = data.size();

  // Expand the runs after the software sync into a stream of half-bit
  // levels. The first low run holds the sync space (604 us) and possibly a
  // merged first data half-bit (Manchester merges consecutive same-level
  // halves), so one low unit is always skipped.
  bool levels[FRAME_BITS_80 * 2 + 1];
  uint32_t units = 0;

  for (int32_t j = sync + 1; j < count && units < units_needed; j++) {
    const int32_t raw = data.peek(j);
    const int32_t duration = raw < 0 ? -raw : raw;
    const int32_t n_units = (duration + HALF_BIT_US / 2) / HALF_BIT_US;
    if (n_units > 2) {
      // Inter-frame gap: when the frame ends with a LOW half-bit it merges
      // with the gap, leaving us one unit short.
      if (raw < 0 && units == units_needed - 1) {
        levels[units++] = false;
      }
      break;
    }
    if (n_units < 1) {
      break;
    }
    if (!near(duration, n_units * HALF_BIT_US, TOLERANCE_PCT)) {
      return false;  // level run does not fit the Manchester clock
    }
    const bool level = raw > 0;
    for (int32_t k = 0; k < n_units && units < units_needed; k++) {
      levels[units++] = level;
    }
  }

  if (units == units_needed - 1) {
    // A capture from remote_receiver carries no inter-frame gap run: a
    // trailing LOW half-bit merges with the silence and never appears in
    // the capture. Complete the stream (the Manchester and checksum
    // checks reject anything else).
    levels[units++] = false;
  }
  if (units < units_needed) {
    return false;
  }

  // Manchester bits: bit 0 = HIGH then LOW, bit 1 = LOW then HIGH.
  uint8_t frame[FRAME_BYTES] = {0, 0, 0, 0, 0, 0, 0, 0, 0, 0};
  for (uint32_t b = 0; b < bits; b++) {
    const bool first = levels[1 + 2 * b];
    const bool second = levels[1 + 2 * b + 1];
    if (first == second) {
      return false;
    }
    if (second) {  // LOW then HIGH encodes a 1
      frame[b / 8] |= 0x80 >> (b % 8);
    }
  }

  // De-obfuscate the rolling XOR applied after the key byte. The
  // obfuscation runs forward, so undoing it must run backward. The
  // extension bytes (7-9) are never obfuscated.
  for (int32_t b = 6; b >= 1; b--) {
    frame[b] ^= frame[b - 1];
  }

  // Verify the standard checksum: XOR of all nibbles of the first 7 bytes,
  // computed with the checksum nibble masked out.
  const uint8_t stored = frame[1] & 0x0F;
  frame[1] &= 0xF0;
  uint8_t checksum = 0;
  for (uint8_t k = 0; k < 7; k++) {
    checksum ^= frame[k] ^ (frame[k] >> 4);
  }
  if ((checksum & 0x0F) != stored) {
    return false;
  }

  // Verify the 80-bit extension checksum.
  if (bits == FRAME_BITS_80) {
    if ((frame[9] & 0x0F) != checksum80(frame[7], frame[8], frame[9])) {
      return false;
    }
  }

  // Translate the control nibble, applying the 80-bit extensions:
  //   My    + byte 8 low nibble  -> Stop (0xF1) / Favorite (0xC1)
  //   0x0B  + byte 8 bit 3      -> Step Up (0x8B) / Step Down (0x0B)
  uint8_t command = frame[1] >> 4;
  uint16_t step = 0;
  if (bits == FRAME_BITS_80) {
    if (command == 0x1) {
      command |= (frame[8] & 0x0F) << 4;
    } else if (command == 0xB) {
      command |= (frame[8] & 0x08) << 4;
      step = ((frame[8] & 0x07) << 4) | ((frame[9] & 0xF0) >> 4);
    }
  }

  const uint16_t rolling_code = static_cast<uint16_t>((frame[2] << 8) | frame[3]);
  const uint32_t address =
      (static_cast<uint32_t>(frame[4]) << 16) | (static_cast<uint32_t>(frame[5]) << 8) | frame[6];

  // Suppress duplicates: each press repeats the same rolling code on air.
  const uint32_t now = millis();
  if (address == this->last_address_ && rolling_code == this->last_rolling_code_ &&
      now - this->last_rx_millis_ < 1500) {
    return true;
  }
  this->last_address_ = address;
  this->last_rolling_code_ = rolling_code;
  this->last_rx_millis_ = now;

  ESP_LOGD(TAG, "RTS frame: address=%" PRIu32 " command=0x%02X rolling_code=%" PRIu16 " bits=%" PRIu32
               " step=%" PRIu16,
           address, command, rolling_code, bits, step);
  this->command_trigger_->trigger(address, command, rolling_code, bits, step);
  return true;
}

}  // namespace esphome::somfy_rts
