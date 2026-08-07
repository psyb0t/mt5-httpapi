package mt5httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"

	"github.com/psyb0t/aichteeteapee"
)

func TestClient_GetAccount(t *testing.T) {
	t.Parallel()

	mux := http.NewServeMux()
	mux.HandleFunc("/account", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("unexpected method: %s", r.Method)
		}

		body := map[string]any{
			"login":              100,
			"trade_mode":         107,
			"leverage":           114,
			"limit_orders":       121,
			"margin_so_mode":     128,
			"trade_allowed":      true,
			"trade_expert":       true,
			"margin_mode":        149,
			"currency_digits":    156,
			"fifo_close":         true,
			"balance":            130.25,
			"credit":             133.25,
			"profit":             136.25,
			"equity":             139.25,
			"margin":             142.25,
			"margin_free":        145.25,
			"margin_level":       148.25,
			"margin_so_call":     151.25,
			"margin_so_so":       154.25,
			"margin_initial":     157.25,
			"margin_maintenance": 160.25,
			"assets":             163.25,
			"liabilities":        166.25,
			"commission_blocked": 169.25,
			"name":               "test-name-24",
			"server":             "test-server-25",
			"currency":           "TESTEUR",
			"company":            "test-company-27",
		}

		if err := json.NewEncoder(w).Encode(body); err != nil {
			t.Errorf("encode account response: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	got, err := client.GetAccount(context.Background())
	if err != nil {
		t.Fatalf("get account: %v", err)
	}

	want := &Account{
		Login:         100,
		TradeMode:     107,
		Leverage:      114,
		LimitOrders:   121,
		MarginSOMode:  128,
		TradeAllowed:  true,
		TradeExpert:   true,
		MarginMode:    149,
		CurrencyDigit: 156,
		FIFOClose:     true,
		Balance:       130.25,
		Credit:        133.25,
		Profit:        136.25,
		Equity:        139.25,
		Margin:        142.25,
		MarginFree:    145.25,
		MarginLevel:   148.25,
		MarginSOCall:  151.25,
		MarginSOSO:    154.25,
		MarginInitial: 157.25,
		MarginMaint:   160.25,
		Assets:        163.25,
		Liabilities:   166.25,
		CommissionBlk: 169.25,
		Name:          "test-name-24",
		Server:        "test-server-25",
		Currency:      "TESTEUR",
		Company:       "test-company-27",
	}

	if !reflect.DeepEqual(got, want) {
		t.Fatalf("unexpected account:\ngot  %#v\nwant %#v", got, want)
	}
}

func TestClient_GetAccount_ErrorMapping(t *testing.T) {
	t.Parallel()

	testCases := []struct {
		name       string
		status     int
		apiMessage string
		wantErr    error
	}{
		{
			name: "bad request", status: http.StatusBadRequest,
			apiMessage: "invalid login", wantErr: aichteeteapee.ErrBadRequest,
		},
		{
			name: "unauthorized", status: http.StatusUnauthorized,
			apiMessage: "no token", wantErr: aichteeteapee.ErrUnauthorized,
		},
		{
			name: "forbidden", status: http.StatusForbidden,
			apiMessage: "denied", wantErr: aichteeteapee.ErrForbidden,
		},
		{
			name: "not found", status: http.StatusNotFound,
			apiMessage: "account missing", wantErr: aichteeteapee.ErrNotFound,
		},
		{
			name: "method not allowed", status: http.StatusMethodNotAllowed,
			apiMessage: "", wantErr: aichteeteapee.ErrMethodNotAllowed,
		},
		{
			name: "conflict", status: http.StatusConflict,
			apiMessage: "busy", wantErr: aichteeteapee.ErrConflict,
		},
		{
			name: "unprocessable entity", status: http.StatusUnprocessableEntity,
			apiMessage: "bad shape", wantErr: aichteeteapee.ErrUnprocessableEntity,
		},
		{
			name: "too many requests", status: http.StatusTooManyRequests,
			apiMessage: "slow down", wantErr: aichteeteapee.ErrTooManyRequests,
		},
		{
			name: "internal server error", status: http.StatusInternalServerError,
			apiMessage: "boom", wantErr: aichteeteapee.ErrInternalServer,
		},
		{
			name: "bad gateway", status: http.StatusBadGateway,
			apiMessage: "upstream down", wantErr: aichteeteapee.ErrBadGateway,
		},
		{
			name: "service unavailable", status: http.StatusServiceUnavailable,
			apiMessage: "mt5 not ready", wantErr: ErrNotInitialized,
		},
		{
			name: "gateway timeout", status: http.StatusGatewayTimeout,
			apiMessage: "timed out", wantErr: aichteeteapee.ErrGatewayTimeout,
		},
		{
			name: "unmapped status", status: http.StatusTeapot,
			apiMessage: "unexpected", wantErr: aichteeteapee.ErrUnexpectedResponseStatus,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			mux := http.NewServeMux()
			mux.HandleFunc("/account", func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(tc.status)

				if tc.apiMessage == "" {
					return
				}

				if err := json.NewEncoder(w).Encode(APIError{Error: tc.apiMessage}); err != nil {
					t.Errorf("encode error body: %v", err)
				}
			})

			server := httptest.NewServer(mux)
			t.Cleanup(server.Close)

			client, err := New(Config{BaseURL: server.URL})
			if err != nil {
				t.Fatalf("create client: %v", err)
			}

			_, err = client.GetAccount(context.Background())
			if err == nil {
				t.Fatalf("status %d: expected error, got nil", tc.status)
			}

			if !errors.Is(err, tc.wantErr) {
				t.Fatalf("status %d: got err %v, want errors.Is match for %v", tc.status, err, tc.wantErr)
			}

			if tc.apiMessage != "" && !strings.Contains(err.Error(), tc.apiMessage) {
				t.Fatalf("status %d: error %q does not contain api message %q", tc.status, err.Error(), tc.apiMessage)
			}
		})
	}
}
