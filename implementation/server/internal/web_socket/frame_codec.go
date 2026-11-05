// SPDX-License-Identifier: MIT
package web_socket

import (
	"context"
	"encoding/binary"
	"github.com/gogf/gf/v2/frame/g"
)

// Parse custom binary protocol messages, return message type, data length, payload and success status
func parseBinaryMessage(ctx context.Context, msg *[]byte) (byte, int, []byte, bool) {
	if msg == nil || len(*msg) < 1+4 {
		g.Log().Warning(ctx, "Message too short, cannot parse header, message not forwarded")
		return 0, 0, nil, false
	}

	msgType := (*msg)[0]
	declaredLen := binary.BigEndian.Uint32((*msg)[1:5])
	dataLen := len(*msg) - 5
	if uint64(dataLen) != uint64(declaredLen) {
		g.Log().Warningf(ctx, "Length mismatch: header says %d, actual is %d, message not forwarded", declaredLen, dataLen)
		return 0, 0, nil, false
	}

	payload := (*msg)[5:]
	return msgType, dataLen, payload, true
}
