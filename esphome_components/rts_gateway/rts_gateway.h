#pragma once

#include "esphome/core/automation.h"
#include "esphome/core/component.h"
#include "esphome/components/remote_base/remote_base.h"

namespace esphome::rts_gateway {

/// RTS half-bit duration in microseconds
constexpr uint32_t HALF_BIT_US = 604;
/// Hardware sync pulse duration in microseconds
constexpr uint32_t HW_SYNC_US = 2416;
/// Software sync mark duration in microseconds
constexpr uint32_t SW_SYNC_MARK_US = 4550;
/// Standard frame bit length
constexpr uint32_t FRAME_BITS = 56;
/// Extended (step-wheel remote) frame bit length
constexpr uint32_t FRAME_BITS_80 = 80;
/// Maximum frame bytes (80-bit frame)
constexpr uint32_t FRAME_BYTES = 10;
/// Matching tolerance for pulse classification, in percent
constexpr uint32_t TOLERANCE_PCT = 30;
/// Minimum hardware sync runs before the software sync
constexpr uint32_t MIN_HW_RUNS = 4;

/// Trigger fired with (address, command, rolling_code, bits, step) on each
/// decoded frame. ``command`` uses the extended command byte for 80-bit
/// frames (0x8B Step Up, 0xF1 Stop, 0xC1 Favorite); ``step`` carries the
/// step size for step commands (0 otherwise).
class RTSGatewayTrigger
    : public Trigger<uint32_t, uint8_t, uint16_t, uint8_t, uint16_t> {};

/// Decodes RTS frames (56 and 80 bit) captured by a remote_receiver.
///
/// The receiver pin must be fed with the OOK baseband signal of the target
/// frequency (433.42 MHz): either the GDO0 pin of a CC1101 configured for
/// OOK reception, or a 433 MHz OOK receiver module.
class RTSGatewayComponent : public Component, public remote_base::RemoteReceiverListener {
 public:
  void setup() override;
  void dump_config() override;

  /// Attach the remote receiver to listen to.
  void set_receiver(remote_base::RemoteReceiverBase *receiver) { this->receiver_ = receiver; }
  /// Automation trigger for decoded frames.
  RTSGatewayTrigger *get_command_trigger() const { return this->command_trigger_; }

  bool on_receive(remote_base::RemoteReceiveData data) override;

 protected:
  /// Decode a frame whose software sync mark starts at run index `sync`.
  /// ``hw_runs`` is the number of hardware sync runs preceding the sync.
  bool decode_frame_(const remote_base::RemoteReceiveData &data, uint32_t sync, uint32_t hw_runs);

  remote_base::RemoteReceiverBase *receiver_{nullptr};
  RTSGatewayTrigger *command_trigger_{new RTSGatewayTrigger()};

  /// Duplicate frame suppression: an RTS press is repeated on air several
  /// times with the same rolling code.
  uint32_t last_address_{0};
  uint16_t last_rolling_code_{0};
  uint32_t last_rx_millis_{0};
};

}  // namespace esphome::rts_gateway
