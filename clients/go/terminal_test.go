package mt5httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestTerminalResponsesDecodeCurrentFields(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("/ping", func(w http.ResponseWriter, _ *http.Request) {
		if err := json.NewEncoder(w).Encode(map[string]any{
			"status": "ok",
			"mode":   "backtest",
		}); err != nil {
			t.Fatalf("encode ping response: %v", err)
		}
	})
	mux.HandleFunc("/terminal", func(w http.ResponseWriter, _ *http.Request) {
		if err := json.NewEncoder(w).Encode(map[string]any{
			"broker_utc_offset_hours":   3.5,
			"broker_utc_offset_seconds": 12600,
		}); err != nil {
			t.Fatalf("encode terminal response: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	ping, err := client.Ping(context.Background())
	if err != nil {
		t.Fatalf("ping: %v", err)
	}
	if ping.Status != "ok" || ping.Mode != "backtest" {
		t.Fatalf("unexpected ping response: %#v", ping)
	}

	terminal, err := client.GetTerminal(context.Background())
	if err != nil {
		t.Fatalf("get terminal: %v", err)
	}
	if terminal.BrokerUTCOffsetHours != 3.5 {
		t.Fatalf("unexpected UTC offset hours: %v", terminal.BrokerUTCOffsetHours)
	}
	if terminal.BrokerUTCOffsetSeconds != 12600 {
		t.Fatalf("unexpected UTC offset seconds: %v", terminal.BrokerUTCOffsetSeconds)
	}
}
