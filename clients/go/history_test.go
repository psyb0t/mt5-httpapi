package mt5httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"net/url"
	"reflect"
	"strings"
	"testing"

	"github.com/psyb0t/aichteeteapee"
)

func historyFullOrderJSON() map[string]any {
	return map[string]any{
		"ticket":          100,
		"time_setup":      107,
		"time_setup_msc":  114,
		"time_done":       121,
		"time_done_msc":   128,
		"time_expiration": 135,
		"type":            142,
		"type_time":       149,
		"type_filling":    156,
		"state":           163,
		"magic":           170,
		"position_id":     177,
		"position_by_id":  184,
		"reason":          191,
		"volume_initial":  142.25,
		"volume_current":  145.25,
		"price_open":      148.25,
		"sl":              151.25,
		"tp":              154.25,
		"price_current":   157.25,
		"price_stoplimit": 160.25,
		"symbol":          "TESTCHF",
		"comment":         "test-history-order-comment",
		"external_id":     "test-history-order-external-id",
	}
}

func historyFullOrderWant() Order {
	return Order{
		Ticket:         100,
		TimeSetup:      107,
		TimeSetupMsc:   114,
		TimeDone:       121,
		TimeDoneMsc:    128,
		TimeExpiration: 135,
		Type:           142,
		TypeTime:       149,
		TypeFilling:    156,
		State:          163,
		Magic:          170,
		PositionID:     177,
		PositionByID:   184,
		Reason:         191,
		VolumeInitial:  142.25,
		VolumeCurrent:  145.25,
		PriceOpen:      148.25,
		SL:             151.25,
		TP:             154.25,
		PriceCurrent:   157.25,
		PriceStopLimit: 160.25,
		Symbol:         "TESTCHF",
		Comment:        "test-history-order-comment",
		ExternalID:     "test-history-order-external-id",
	}
}

func historyFullDealJSON() map[string]any {
	return map[string]any{
		"ticket":      100,
		"order":       107,
		"time":        114,
		"time_msc":    121,
		"type":        128,
		"entry":       135,
		"magic":       142,
		"position_id": 149,
		"reason":      156,
		"volume":      127.25,
		"price":       130.25,
		"commission":  133.25,
		"swap":        136.25,
		"profit":      139.25,
		"fee":         142.25,
		"sl":          145.25,
		"tp":          148.25,
		"symbol":      "TESTGBP",
		"comment":     "test-history-deal-comment",
		"external_id": "test-history-deal-external-id",
	}
}

func historyFullDealWant() Deal {
	return Deal{
		Ticket:     100,
		Order:      107,
		Time:       114,
		TimeMsc:    121,
		Type:       128,
		Entry:      135,
		Magic:      142,
		PositionID: 149,
		Reason:     156,
		Volume:     127.25,
		Price:      130.25,
		Commission: 133.25,
		Swap:       136.25,
		Profit:     139.25,
		Fee:        142.25,
		SL:         145.25,
		TP:         148.25,
		Symbol:     "TESTGBP",
		Comment:    "test-history-deal-comment",
		ExternalID: "test-history-deal-external-id",
	}
}

func TestClient_HistoryOrders(t *testing.T) {
	t.Parallel()

	wantQuery := url.Values{"from": {"1700000000"}, "to": {"1700003600"}}

	mux := http.NewServeMux()
	mux.HandleFunc("/history/orders", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("unexpected method: %s", r.Method)
		}

		if !reflect.DeepEqual(r.URL.Query(), wantQuery) {
			t.Errorf("unexpected query:\ngot  %#v\nwant %#v", r.URL.Query(), wantQuery)
		}

		body := []map[string]any{historyFullOrderJSON()}
		if err := json.NewEncoder(w).Encode(body); err != nil {
			t.Errorf("encode history orders response: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	got, err := client.HistoryOrders(context.Background(), HistoryQuery{From: 1700000000, To: 1700003600})
	if err != nil {
		t.Fatalf("history orders: %v", err)
	}

	want := []Order{historyFullOrderWant()}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("unexpected history orders:\ngot  %#v\nwant %#v", got, want)
	}
}

func TestClient_HistoryDeals(t *testing.T) {
	t.Parallel()

	wantQuery := url.Values{"from": {"1700000000"}, "to": {"1700003600"}}

	mux := http.NewServeMux()
	mux.HandleFunc("/history/deals", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("unexpected method: %s", r.Method)
		}

		if !reflect.DeepEqual(r.URL.Query(), wantQuery) {
			t.Errorf("unexpected query:\ngot  %#v\nwant %#v", r.URL.Query(), wantQuery)
		}

		body := []map[string]any{historyFullDealJSON()}
		if err := json.NewEncoder(w).Encode(body); err != nil {
			t.Errorf("encode history deals response: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	got, err := client.HistoryDeals(context.Background(), HistoryQuery{From: 1700000000, To: 1700003600})
	if err != nil {
		t.Fatalf("history deals: %v", err)
	}

	want := []Deal{historyFullDealWant()}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("unexpected history deals:\ngot  %#v\nwant %#v", got, want)
	}
}

func TestClient_History_ErrorMapping(t *testing.T) {
	t.Parallel()

	testCases := []struct {
		name       string
		status     int
		apiMessage string
		wantErr    error
	}{
		{
			name: "bad request", status: http.StatusBadRequest,
			apiMessage: "invalid range", wantErr: aichteeteapee.ErrBadRequest,
		},
		{
			name: "service unavailable", status: http.StatusServiceUnavailable,
			apiMessage: "mt5 not ready", wantErr: ErrNotInitialized,
		},
		{
			name: "internal server error", status: http.StatusInternalServerError,
			apiMessage: "boom", wantErr: aichteeteapee.ErrInternalServer,
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
			mux.HandleFunc("/history/orders", func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(tc.status)

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

			_, err = client.HistoryOrders(context.Background(), HistoryQuery{From: 1, To: 2})
			if err == nil {
				t.Fatalf("status %d: expected error, got nil", tc.status)
			}

			if !errors.Is(err, tc.wantErr) {
				t.Fatalf("status %d: got err %v, want errors.Is match for %v", tc.status, err, tc.wantErr)
			}

			if !strings.Contains(err.Error(), tc.apiMessage) {
				t.Fatalf("status %d: error %q does not contain api message %q", tc.status, err.Error(), tc.apiMessage)
			}
		})
	}
}

func TestClient_HistoryDeals_Error(t *testing.T) {
	t.Parallel()

	mux := http.NewServeMux()
	mux.HandleFunc("/history/deals", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)

		if err := json.NewEncoder(w).Encode(APIError{Error: "no deals"}); err != nil {
			t.Errorf("encode error body: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	_, err = client.HistoryDeals(context.Background(), HistoryQuery{From: 1, To: 2})
	if !errors.Is(err, aichteeteapee.ErrNotFound) {
		t.Fatalf("got err %v, want errors.Is match for %v", err, aichteeteapee.ErrNotFound)
	}

	if !strings.Contains(err.Error(), "no deals") {
		t.Fatalf("error %q does not contain api message", err.Error())
	}
}
