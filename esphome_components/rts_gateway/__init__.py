"""RTS decoder component for ESPHome.

Listens to a ``remote_receiver`` fed by the OOK baseband output of a CC1101
(or any 433.42 MHz OOK receiver), decodes RTS frames and fires an
``on_command`` automation trigger with the frame fields.
"""

from __future__ import annotations

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome import automation
from esphome.components import remote_base
from esphome.const import CONF_ID

CODEOWNERS = ["@xavier-rettel"]
AUTO_LOAD = ["remote_base"]

rts_gateway_ns = cg.esphome_ns.namespace("rts_gateway")
RTSGatewayComponent = rts_gateway_ns.class_("RTSGatewayComponent", cg.Component)

CONF_RECEIVER_ID = "receiver_id"
CONF_ON_COMMAND = "on_command"

CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(RTSGatewayComponent),
        cv.Required(CONF_RECEIVER_ID): cv.use_id(remote_base.RemoteReceiverBase),
        cv.Required(CONF_ON_COMMAND): automation.validate_automation({}),
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    """Generate code for the RTS component."""
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    # Links the receiver and registers the component as a listener with a
    # counted slot (ESPHome 2026.10 requires add_listener at codegen time;
    # runtime registration from C++ no longer compiles).
    await remote_base.attach_receiver(var, config)

    for conf in config.get(CONF_ON_COMMAND, []):
        await automation.build_automation(
            var.get_command_trigger(),
            [
                (cg.uint32, "address"),
                (cg.uint8, "command"),
                (cg.uint16, "rolling_code"),
                (cg.uint8, "bits"),
                (cg.uint16, "step"),
            ],
            conf,
        )
