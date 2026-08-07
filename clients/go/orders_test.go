package mt5httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"

	"github.com/psyb0t/aichteeteapee"
)

func ordersFullOrderJSON() map[string]any {
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
		"symbol":          "TESTEUR",
		"comment":         "test-comment-22",
		"external_id":     "test-external_id-23",
	}
}

func ordersFullOrderWant() Order {
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
		Symbol:         "TESTEUR",
		Comment:        "test-comment-22",
		ExternalID:     "test-external_id-23",
	}
}

func ordersFullTradeResultJSON() map[string]any {
	return map[string]any{
		"retcode":          100,
		"deal":             107,
		"order":            114,
		"volume":           109.25,
		"price":            112.25,
		"bid":              115.25,
		"ask":              118.25,
		"comment":          "test-comment-7",
		"request_id":       156,
		"retcode_external": 163,
	}
}

func ordersFullTradeResultWant() TradeResult {
	return TradeResult{
		Retcode:    100,
		Deal:       107,
		Order:      114,
		Volume:     109.25,
		Price:      112.25,
		Bid:        115.25,
		Ask:        118.25,
		Comment:    "test-comment-7",
		RequestID:  156,
		RetcodeExt: 163,
	}
}

func ordersAssertJSONField(t *testing.T, body map[string]any, tag string, want any) {
	t.Helper()

	got, ok := body[tag]
	if !ok {
		t.Errorf("missing field %q in request body: %#v", tag, body)
		return
	}

	switch w := want.(type) {
	case float64:
		gotFloat, ok := got.(float64)
		if !ok || gotFloat != w {
			t.Errorf("field %q: got %#v, want %v", tag, got, w)
		}
	case string:
		gotStr, ok := got.(string)
		if !ok || gotStr != w {
			t.Errorf("field %q: got %#v, want %q", tag, got, w)
		}
	default:
		t.Errorf("unsupported want type %T for field %q", want, tag)
	}
}

func ordersAssertJSONFieldAbsent(t *testing.T, body map[string]any, tag string) {
	t.Helper()

	if _, ok := body[tag]; ok {
		t.Errorf("field %q should be omitted, got %#v", tag, body[tag])
	}
}

func TestClient_ListOrders(t *testing.T) {
	t.Parallel()

	testCases := []struct {
		name      string
		symbol    string
		wantQuery bool
	}{
		{name: "with symbol filter", symbol: "TESTUSD", wantQuery: true},
		{name: "without symbol filter", symbol: "", wantQuery: false},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			mux := http.NewServeMux()
			mux.HandleFunc("/orders", func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodGet {
					t.Errorf("unexpected method: %s", r.Method)
				}

				values, hasSymbol := r.URL.Query()["symbol"]
				if tc.wantQuery {
					if !hasSymbol || len(values) != 1 || values[0] != tc.symbol {
						t.Errorf("unexpected symbol query: %v", r.URL.Query())
					}
				} else if hasSymbol {
					t.Errorf("unexpected symbol query present: %v", values)
				}

				body := []map[string]any{ordersFullOrderJSON()}
				if err := json.NewEncoder(w).Encode(body); err != nil {
					t.Errorf("encode orders response: %v", err)
				}
			})

			server := httptest.NewServer(mux)
			t.Cleanup(server.Close)

			client, err := New(Config{BaseURL: server.URL})
			if err != nil {
				t.Fatalf("create client: %v", err)
			}

			got, err := client.ListOrders(context.Background(), tc.symbol)
			if err != nil {
				t.Fatalf("list orders: %v", err)
			}

			want := []Order{ordersFullOrderWant()}
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("unexpected orders:\ngot  %#v\nwant %#v", got, want)
			}
		})
	}
}

func TestClient_CreateOrder(t *testing.T) {
	t.Parallel()

	mux := http.NewServeMux()
	mux.HandleFunc("/orders", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Errorf("unexpected method: %s", r.Method)
		}

		if got := r.Header.Get("Content-Type"); got != aichteeteapee.ContentTypeJSON {
			t.Errorf("unexpected content type: %s", got)
		}

		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Errorf("decode request body: %v", err)
			return
		}

		ordersAssertJSONField(t, body, "symbol", "TESTUSD")
		ordersAssertJSONField(t, body, "type", "test-type-1")
		ordersAssertJSONField(t, body, "volume", 106.25)
		ordersAssertJSONField(t, body, "price", 109.25)
		ordersAssertJSONField(t, body, "sl", 112.25)
		ordersAssertJSONField(t, body, "tp", 115.25)
		ordersAssertJSONField(t, body, "deviation", 142.0)
		ordersAssertJSONField(t, body, "magic", 149.0)
		ordersAssertJSONField(t, body, "comment", "test-comment-8")
		ordersAssertJSONField(t, body, "type_filling", "test-type_filling-9")
		ordersAssertJSONField(t, body, "type_time", "test-type_time-10")
		ordersAssertJSONField(t, body, "expiration", 177.0)
		ordersAssertJSONField(t, body, "stoplimit", 136.25)

		if err := json.NewEncoder(w).Encode(ordersFullTradeResultJSON()); err != nil {
			t.Errorf("encode trade result response: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	req := &CreateOrderRequest{
		Symbol:      "TESTUSD",
		Type:        "test-type-1",
		Volume:      106.25,
		Price:       109.25,
		SL:          112.25,
		TP:          115.25,
		Deviation:   142,
		Magic:       149,
		Comment:     "test-comment-8",
		TypeFilling: "test-type_filling-9",
		TypeTime:    "test-type_time-10",
		Expiration:  177,
		StopLimit:   136.25,
	}

	got, err := client.CreateOrder(context.Background(), req)
	if err != nil {
		t.Fatalf("create order: %v", err)
	}

	want := ordersFullTradeResultWant()
	if !reflect.DeepEqual(got, &want) {
		t.Fatalf("unexpected trade result:\ngot  %#v\nwant %#v", got, &want)
	}
}

func TestClient_CreateOrder_OmitEmptyFields(t *testing.T) {
	t.Parallel()

	mux := http.NewServeMux()
	mux.HandleFunc("/orders", func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Errorf("decode request body: %v", err)
			return
		}

		ordersAssertJSONField(t, body, "symbol", "TESTUSD")
		ordersAssertJSONField(t, body, "type", "test-type-1")
		ordersAssertJSONField(t, body, "volume", 1.0)

		for _, tag := range []string{
			"price", "sl", "tp", "deviation", "magic",
			"comment", "type_filling", "type_time", "expiration", "stoplimit",
		} {
			ordersAssertJSONFieldAbsent(t, body, tag)
		}

		if err := json.NewEncoder(w).Encode(ordersFullTradeResultJSON()); err != nil {
			t.Errorf("encode trade result response: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	req := &CreateOrderRequest{
		Symbol: "TESTUSD",
		Type:   "test-type-1",
		Volume: 1.0,
	}

	if _, err := client.CreateOrder(context.Background(), req); err != nil {
		t.Fatalf("create order: %v", err)
	}
}

func TestClient_CreateOrder_NilRequest(t *testing.T) {
	t.Parallel()

	mux := http.NewServeMux()
	mux.HandleFunc("/orders", func(_ http.ResponseWriter, _ *http.Request) {
		t.Errorf("server should not be contacted for a nil request")
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	_, err = client.CreateOrder(context.Background(), nil)
	if !errors.Is(err, ErrNilRequest) {
		t.Fatalf("got err %v, want errors.Is match for %v", err, ErrNilRequest)
	}
}

func TestClient_GetOrder(t *testing.T) {
	t.Parallel()

	const ticket int64 = 555111

	mux := http.NewServeMux()
	mux.HandleFunc("/orders/", func(w http.ResponseWriter, r *http.Request) {
		wantPath := fmt.Sprintf("/orders/%d", ticket)
		if r.URL.Path != wantPath {
			t.Errorf("unexpected path: got %s, want %s", r.URL.Path, wantPath)
		}

		if r.Method != http.MethodGet {
			t.Errorf("unexpected method: %s", r.Method)
		}

		if err := json.NewEncoder(w).Encode(ordersFullOrderJSON()); err != nil {
			t.Errorf("encode order response: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	got, err := client.GetOrder(context.Background(), ticket)
	if err != nil {
		t.Fatalf("get order: %v", err)
	}

	want := ordersFullOrderWant()
	if !reflect.DeepEqual(got, &want) {
		t.Fatalf("unexpected order:\ngot  %#v\nwant %#v", got, &want)
	}
}

func TestClient_UpdateOrder(t *testing.T) {
	t.Parallel()

	const ticket int64 = 555222

	mux := http.NewServeMux()
	mux.HandleFunc("/orders/", func(w http.ResponseWriter, r *http.Request) {
		wantPath := fmt.Sprintf("/orders/%d", ticket)
		if r.URL.Path != wantPath {
			t.Errorf("unexpected path: got %s, want %s", r.URL.Path, wantPath)
		}

		if r.Method != http.MethodPut {
			t.Errorf("unexpected method: %s", r.Method)
		}

		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Errorf("decode request body: %v", err)
			return
		}

		ordersAssertJSONField(t, body, "price", 100.25)
		ordersAssertJSONField(t, body, "sl", 103.25)
		ordersAssertJSONField(t, body, "tp", 106.25)
		ordersAssertJSONField(t, body, "type_time", "test-type_time-3")

		if err := json.NewEncoder(w).Encode(ordersFullTradeResultJSON()); err != nil {
			t.Errorf("encode trade result response: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	req := &UpdateOrderRequest{
		Price:    100.25,
		SL:       103.25,
		TP:       106.25,
		TypeTime: "test-type_time-3",
	}

	got, err := client.UpdateOrder(context.Background(), ticket, req)
	if err != nil {
		t.Fatalf("update order: %v", err)
	}

	want := ordersFullTradeResultWant()
	if !reflect.DeepEqual(got, &want) {
		t.Fatalf("unexpected trade result:\ngot  %#v\nwant %#v", got, &want)
	}
}

func TestClient_UpdateOrder_NilRequest(t *testing.T) {
	t.Parallel()

	mux := http.NewServeMux()
	mux.HandleFunc("/orders/", func(_ http.ResponseWriter, _ *http.Request) {
		t.Errorf("server should not be contacted for a nil request")
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	_, err = client.UpdateOrder(context.Background(), 1, nil)
	if !errors.Is(err, ErrNilRequest) {
		t.Fatalf("got err %v, want errors.Is match for %v", err, ErrNilRequest)
	}
}

func TestClient_CancelOrder(t *testing.T) {
	t.Parallel()

	const ticket int64 = 555333

	mux := http.NewServeMux()
	mux.HandleFunc("/orders/", func(w http.ResponseWriter, r *http.Request) {
		wantPath := fmt.Sprintf("/orders/%d", ticket)
		if r.URL.Path != wantPath {
			t.Errorf("unexpected path: got %s, want %s", r.URL.Path, wantPath)
		}

		if r.Method != http.MethodDelete {
			t.Errorf("unexpected method: %s", r.Method)
		}

		if got := r.Header.Get("Content-Type"); got != "" {
			t.Errorf("unexpected content type for bodyless request: %s", got)
		}

		if err := json.NewEncoder(w).Encode(ordersFullTradeResultJSON()); err != nil {
			t.Errorf("encode trade result response: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	got, err := client.CancelOrder(context.Background(), ticket)
	if err != nil {
		t.Fatalf("cancel order: %v", err)
	}

	want := ordersFullTradeResultWant()
	if !reflect.DeepEqual(got, &want) {
		t.Fatalf("unexpected trade result:\ngot  %#v\nwant %#v", got, &want)
	}
}

func TestClient_Orders_ErrorMapping(t *testing.T) {
	t.Parallel()

	testCases := []struct {
		name       string
		status     int
		apiMessage string
		wantErr    error
	}{
		{
			name: "bad request", status: http.StatusBadRequest,
			apiMessage: "invalid symbol", wantErr: aichteeteapee.ErrBadRequest,
		},
		{
			name: "not found", status: http.StatusNotFound,
			apiMessage: "orders missing", wantErr: aichteeteapee.ErrNotFound,
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
			mux.HandleFunc("/orders", func(w http.ResponseWriter, _ *http.Request) {
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

			_, err = client.ListOrders(context.Background(), "")
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

func TestClient_GetOrder_Error(t *testing.T) {
	t.Parallel()

	const ticket int64 = 999444

	mux := http.NewServeMux()
	mux.HandleFunc("/orders/", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)

		if err := json.NewEncoder(w).Encode(APIError{Error: "no such order"}); err != nil {
			t.Errorf("encode error body: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	_, err = client.GetOrder(context.Background(), ticket)
	if !errors.Is(err, aichteeteapee.ErrNotFound) {
		t.Fatalf("got err %v, want errors.Is match for %v", err, aichteeteapee.ErrNotFound)
	}

	if !strings.Contains(err.Error(), fmt.Sprintf("%d", ticket)) {
		t.Fatalf("error %q does not carry ticket context %d", err.Error(), ticket)
	}
}

func TestClient_UpdateOrder_Error(t *testing.T) {
	t.Parallel()

	const ticket int64 = 999555

	mux := http.NewServeMux()
	mux.HandleFunc("/orders/", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusConflict)

		if err := json.NewEncoder(w).Encode(APIError{Error: "order locked"}); err != nil {
			t.Errorf("encode error body: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	_, err = client.UpdateOrder(context.Background(), ticket, &UpdateOrderRequest{TP: 1})
	if !errors.Is(err, aichteeteapee.ErrConflict) {
		t.Fatalf("got err %v, want errors.Is match for %v", err, aichteeteapee.ErrConflict)
	}

	if !strings.Contains(err.Error(), fmt.Sprintf("%d", ticket)) {
		t.Fatalf("error %q does not carry ticket context %d", err.Error(), ticket)
	}
}

func TestClient_CancelOrder_Error(t *testing.T) {
	t.Parallel()

	const ticket int64 = 999666

	mux := http.NewServeMux()
	mux.HandleFunc("/orders/", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusUnprocessableEntity)

		if err := json.NewEncoder(w).Encode(APIError{Error: "cannot cancel"}); err != nil {
			t.Errorf("encode error body: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	_, err = client.CancelOrder(context.Background(), ticket)
	if !errors.Is(err, aichteeteapee.ErrUnprocessableEntity) {
		t.Fatalf("got err %v, want errors.Is match for %v", err, aichteeteapee.ErrUnprocessableEntity)
	}

	if !strings.Contains(err.Error(), fmt.Sprintf("%d", ticket)) {
		t.Fatalf("error %q does not carry ticket context %d", err.Error(), ticket)
	}
}
