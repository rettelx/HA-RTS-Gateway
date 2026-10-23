#pragma once

#include <cstdint>
#include <vector>

namespace esphome {

// Stub of esphome::Trigger that records trigger() calls for assertions.
template<typename... Ts>
class Trigger {
 public:
  void trigger(Ts... x) { calls_.push_back(std::make_tuple(x...)); }
  std::vector<std::tuple<Ts...>> calls_;
};

}  // namespace esphome
