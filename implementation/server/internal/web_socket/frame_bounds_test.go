package web_socket

import (
	"context"
	"testing"
)

func TestParseBinaryMessageBounds(t *testing.T) {
	cases := [][]byte{
		{}, {0x10}, {0x10, 0, 0, 0, 1},
		{0x10, 0xff, 0xff, 0xff, 0xff},
		{0x10, 0, 0, 0, 0, 1},
	}
	for _, frame := range cases {
		if _, _, _, ok := parseBinaryMessage(context.Background(), &frame); ok {
			t.Fatalf("accepted malformed frame %x", frame)
		}
	}
	frame := []byte{0x10, 0, 0, 0, 0}
	kind, length, payload, ok := parseBinaryMessage(context.Background(), &frame)
	if !ok || kind != 0x10 || length != 0 || len(payload) != 0 {
		t.Fatal("valid ping rejected")
	}
}
