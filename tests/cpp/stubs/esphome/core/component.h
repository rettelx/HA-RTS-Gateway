#pragma once

#include <cstdint>

namespace esphome {

// Stub of esphome::Component covering the surface used by somfy_rts.
class Component {
 public:
  virtual void setup() {}
  virtual void dump_config() {}
  virtual ~Component() = default;
};

// Controllable stand-in for esphome::millis().
inline uint32_t &test_millis() {
  static uint32_t value = 0;
  return value;
}

inline uint32_t millis() { return test_millis(); }

}  // namespace esphome
