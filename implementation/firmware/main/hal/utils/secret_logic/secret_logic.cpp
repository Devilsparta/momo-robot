/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "secret_logic.h"
#include <sdkconfig.h>
#include "../private_pairing/private_pairing.h"

namespace secret_logic {

__attribute__((weak)) std::string get_server_url()
{
#ifdef CONFIG_STACKCHAN_SERVER_URL
    return CONFIG_STACKCHAN_SERVER_URL;
#else
    return "http://localhost:3000";
#endif
}

__attribute__((weak)) std::string generate_auth_token()
{
#ifdef CONFIG_STACKCHAN_PRIVATE_BACKEND
    return private_pairing::authorization();
#else
    return "hi-stack-chan";
#endif
}

__attribute__((weak)) std::string generate_handshake_token(std::string_view data)
{
#ifdef CONFIG_STACKCHAN_PRIVATE_BACKEND
    return "";  // Never disclose private bearer credentials through BLE/LAN.
#else
    return "hi-stack-chan";
#endif
}

}  // namespace secret_logic
