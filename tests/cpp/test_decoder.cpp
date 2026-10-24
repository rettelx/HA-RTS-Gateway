// Host-side unit tests for the real Somfy RTS decoder
// (esphome_components/rts_gateway/rts_gateway.cpp) using stub ESPHome
// headers and vectors generated from the Python twin.

#include <cstdint>
#include <cstdio>
#include <tuple>
#include <vector>

#include "rts_gateway.h"
#include "vectors.hpp"

using esphome::remote_base::RemoteReceiveData;
using esphome::rts_gateway::RTSGatewayComponent;

static int failures = 0;

static void check(bool ok, const char *what, const char *name) {
  if (!ok) {
    std::fprintf(stderr, "FAIL [%s]: %s\n", name, what);
    failures++;
  }
}

int main() {
  // 1. Every vector must trigger exactly once with the expected fields.
  for (const auto &v : TEST_VECTORS) {
    esphome::test_millis() += 2000;  // outside the duplicate window
    RTSGatewayComponent comp;
    comp.on_receive(RemoteReceiveData(v.timings));
    const auto &calls = comp.get_command_trigger()->calls_;
    check(calls.size() == 1, "expected exactly one trigger", v.name);
    if (calls.size() != 1) {
      continue;
    }
    const auto &call = calls[0];
    check(std::get<0>(call) == v.address, "address mismatch", v.name);
    check(std::get<1>(call) == v.command, "command mismatch", v.name);
    check(std::get<2>(call) == v.rolling_code, "rolling code mismatch", v.name);
    check(std::get<3>(call) == v.bits, "bits mismatch", v.name);
    check(std::get<4>(call) == v.step, "step mismatch", v.name);
  }

  // 2. Duplicate suppression: same frame within 1.5 s fires once, then again.
  {
    const auto &v = TEST_VECTORS[0];
    esphome::test_millis() += 2000;
    RTSGatewayComponent comp;
    comp.on_receive(RemoteReceiveData(v.timings));
    comp.on_receive(RemoteReceiveData(v.timings));
    check(comp.get_command_trigger()->calls_.size() == 1,
          "duplicate frame not suppressed", v.name);
    esphome::test_millis() += 1600;
    comp.on_receive(RemoteReceiveData(v.timings));
    check(comp.get_command_trigger()->calls_.size() == 2,
          "no retrigger after the duplicate window", v.name);
  }

  // 3. A corrupted frame must not fire.
  {
    std::vector<int32_t> bad = TEST_VECTORS[0].timings;
    bad[bad.size() / 2] = -bad[bad.size() / 2];
    esphome::test_millis() += 2000;
    RTSGatewayComponent comp;
    comp.on_receive(RemoteReceiveData(bad));
    check(comp.get_command_trigger()->calls_.empty(),
          "corrupted frame fired", "corrupted");
  }

  if (failures == 0) {
    std::printf("C++ decoder tests: ALL PASS (%zu vectors)\n", TEST_VECTOR_COUNT);
    return 0;
  }
  std::printf("C++ decoder tests: %d FAILURES\n", failures);
  return 1;
}
