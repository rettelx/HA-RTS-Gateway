#pragma once

#include <cstdint>
#include <vector>

namespace esphome {
namespace remote_base {

// Stub mirroring the real RemoteReceiveData semantics (2026.10):
// holds a reference to the raw capture, peek() indexes relative to 0.
class RemoteReceiveData {
 public:
  explicit RemoteReceiveData(const std::vector<int32_t> &data) : data_(data) {}
  int32_t peek(uint32_t offset = 0) const { return this->data_[this->index_ + offset]; }
  int32_t size() const { return static_cast<int32_t>(this->data_.size()); }

 private:
  const std::vector<int32_t> &data_;
  uint32_t index_{0};
};

class RemoteReceiverListener {
 public:
  virtual bool on_receive(RemoteReceiveData data) = 0;
  virtual ~RemoteReceiverListener() = default;
};

class RemoteReceiverBase {
 public:
  virtual ~RemoteReceiverBase() = default;
};

}  // namespace remote_base
}  // namespace esphome
