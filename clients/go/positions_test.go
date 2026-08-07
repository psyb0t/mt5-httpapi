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

func positionsFullPositionJSON() map[string]any {
	return map[string]any{
		"ticket":          100,
		"time":            107,
		"time_msc":        114,
		"time_update":     121,
		"time_update_msc": 128,
		"type":            135,
		"magic":           142,
		"identifier":      149,
		"reason":          156,
		"volume":          127.25,
		"price_open":      130.25,
		"sl":              133.25,
		"tp":              136.25,
		"price_current":   139.25,
		"swap":            142.25,
		"profit":          145.25,
		"symbol":          "TESTEUR",
		"comment":         "test-comment-17",
		"external_id":     "test-external_id-18",
	}
}

func positionsFullPositionWant() Position {
	return Position{
		Ticket:       100,
		Time:         107,
		TimeMsc:      114,
		TimeUpdate:   121,
		TimeUpdMsc:   128,
		Type:         135,
		Magic:        142,
		Identifier:   149,
		Reason:       156,
		Volume:       127.25,
		PriceOpen:    130.25,
		SL:           133.25,
		TP:           136.25,
		PriceCurrent: 139.25,
		Swap:         142.25,
		Profit:       145.25,
		Symbol:       "TESTEUR",
		Comment:      "test-comment-17",
		ExternalID:   "test-external_id-18",
	}
}

func positionsFullTradeResultJSON() map[string]any {
	return map[string]any{
		"retcode":          200,
		"deal":             207,
		"order":            214,
		"volume":           209.25,
		"price":            212.25,
		"bid":              215.25,
		"ask":              218.25,
		"comment":          "test-comment-position-close",
		"request_id":       256,
		"retcode_external": 263,
	}
}

func positionsFullTradeResultWant() TradeResult {
	return TradeResult{
		Retcode:    200,
		Deal:       207,
		Order:      214,
		Volume:     209.25,
		Price:      212.25,
		Bid:        215.25,
		Ask:        218.25,
		Comment:    "test-comment-position-close",
		RequestID:  256,
		RetcodeExt: 263,
	}
}

func positionsAssertJSONField(t *testing.T, body map[string]any, tag string, want any) {
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

func positionsAssertJSONFieldAbsent(t *testing.T, body map[string]any, tag string) {
	t.Helper()

	if _, ok := body[tag]; ok {
		t.Errorf("field %q should be omitted, got %#v", tag, body[tag])
	}
}

func TestClient_ListPositions(t *testing.T) {
	t.Parallel()

	testCases := []struct {
		name      string
		symbol    string
		wantQuery bool
	}{
		{name: "with symbol filter", symbol: "TESTGBP", wantQuery: true},
		{name: "without symbol filter", symbol: "", wantQuery: false},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			mux := http.NewServeMux()
			mux.HandleFunc("/positions", func(w http.ResponseWriter, r *http.Request) {
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

				body := []map[string]any{positionsFullPositionJSON()}
				if err := json.NewEncoder(w).Encode(body); err != nil {
					t.Errorf("encode positions response: %v", err)
				}
			})

			server := httptest.NewServer(mux)
			t.Cleanup(server.Close)

			client, err := New(Config{BaseURL: server.URL})
			if err != nil {
				t.Fatalf("create client: %v", err)
			}

			got, err := client.ListPositions(context.Background(), tc.symbol)
			if err != nil {
				t.Fatalf("list positions: %v", err)
			}

			want := []Position{positionsFullPositionWant()}
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("unexpected positions:\ngot  %#v\nwant %#v", got, want)
			}
		})
	}
}

func TestClient_GetPosition(t *testing.T) {
	t.Parallel()

	const ticket int64 = 777111

	mux := http.NewServeMux()
	mux.HandleFunc("/positions/", func(w http.ResponseWriter, r *http.Request) {
		wantPath := fmt.Sprintf("/positions/%d", ticket)
		if r.URL.Path != wantPath {
			t.Errorf("unexpected path: got %s, want %s", r.URL.Path, wantPath)
		}

		if r.Method != http.MethodGet {
			t.Errorf("unexpected method: %s", r.Method)
		}

		if err := json.NewEncoder(w).Encode(positionsFullPositionJSON()); err != nil {
			t.Errorf("encode position response: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	got, err := client.GetPosition(context.Background(), ticket)
	if err != nil {
		t.Fatalf("get position: %v", err)
	}

	want := positionsFullPositionWant()
	if !reflect.DeepEqual(got, &want) {
		t.Fatalf("unexpected position:\ngot  %#v\nwant %#v", got, &want)
	}
}

func TestClient_UpdatePosition(t *testing.T) {
	t.Parallel()

	const ticket int64 = 777222

	mux := http.NewServeMux()
	mux.HandleFunc("/positions/", func(w http.ResponseWriter, r *http.Request) {
		wantPath := fmt.Sprintf("/positions/%d", ticket)
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

		positionsAssertJSONField(t, body, "sl", 100.25)
		positionsAssertJSONField(t, body, "tp", 103.25)

		if err := json.NewEncoder(w).Encode(positionsFullTradeResultJSON()); err != nil {
			t.Errorf("encode trade result response: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	req := &UpdatePositionRequest{SL: 100.25, TP: 103.25}

	got, err := client.UpdatePosition(context.Background(), ticket, req)
	if err != nil {
		t.Fatalf("update position: %v", err)
	}

	want := positionsFullTradeResultWant()
	if !reflect.DeepEqual(got, &want) {
		t.Fatalf("unexpected trade result:\ngot  %#v\nwant %#v", got, &want)
	}
}

func TestClient_UpdatePosition_NilRequest(t *testing.T) {
	t.Parallel()

	mux := http.NewServeMux()
	mux.HandleFunc("/positions/", func(_ http.ResponseWriter, _ *http.Request) {
		t.Errorf("server should not be contacted for a nil request")
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	_, err = client.UpdatePosition(context.Background(), 1, nil)
	if !errors.Is(err, ErrNilRequest) {
		t.Fatalf("got err %v, want errors.Is match for %v", err, ErrNilRequest)
	}
}

func TestClient_ClosePosition(t *testing.T) {
	t.Parallel()

	testCases := []struct {
		name       string
		req        *ClosePositionRequest
		wantVolume bool
	}{
		{
			name:       "with body",
			req:        &ClosePositionRequest{Volume: 105.25, Deviation: 142},
			wantVolume: true,
		},
		{
			name:       "without body",
			req:        nil,
			wantVolume: false,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			const ticket int64 = 777333

			mux := http.NewServeMux()
			mux.HandleFunc("/positions/", func(w http.ResponseWriter, r *http.Request) {
				wantPath := fmt.Sprintf("/positions/%d", ticket)
				if r.URL.Path != wantPath {
					t.Errorf("unexpected path: got %s, want %s", r.URL.Path, wantPath)
				}

				if r.Method != http.MethodDelete {
					t.Errorf("unexpected method: %s", r.Method)
				}

				contentType := r.Header.Get("Content-Type")
				if tc.wantVolume {
					if contentType != aichteeteapee.ContentTypeJSON {
						t.Errorf("unexpected content type: %s", contentType)
					}

					var body map[string]any
					if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
						t.Errorf("decode request body: %v", err)
						return
					}

					positionsAssertJSONField(t, body, "volume", 105.25)
					positionsAssertJSONField(t, body, "deviation", 142.0)
				} else if contentType != "" {
					t.Errorf("unexpected content type for bodyless request: %s", contentType)
				}

				if err := json.NewEncoder(w).Encode(positionsFullTradeResultJSON()); err != nil {
					t.Errorf("encode trade result response: %v", err)
				}
			})

			server := httptest.NewServer(mux)
			t.Cleanup(server.Close)

			client, err := New(Config{BaseURL: server.URL})
			if err != nil {
				t.Fatalf("create client: %v", err)
			}

			got, err := client.ClosePosition(context.Background(), ticket, tc.req)
			if err != nil {
				t.Fatalf("close position: %v", err)
			}

			want := positionsFullTradeResultWant()
			if !reflect.DeepEqual(got, &want) {
				t.Fatalf("unexpected trade result:\ngot  %#v\nwant %#v", got, &want)
			}
		})
	}
}

func TestClient_ClosePosition_OmitEmptyFields(t *testing.T) {
	t.Parallel()

	mux := http.NewServeMux()
	mux.HandleFunc("/positions/", func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Errorf("decode request body: %v", err)
			return
		}

		positionsAssertJSONFieldAbsent(t, body, "volume")
		positionsAssertJSONFieldAbsent(t, body, "deviation")

		if err := json.NewEncoder(w).Encode(positionsFullTradeResultJSON()); err != nil {
			t.Errorf("encode trade result response: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	if _, err := client.ClosePosition(context.Background(), 1, &ClosePositionRequest{}); err != nil {
		t.Fatalf("close position: %v", err)
	}
}

func TestClient_Positions_ErrorMapping(t *testing.T) {
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
			apiMessage: "positions missing", wantErr: aichteeteapee.ErrNotFound,
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
			mux.HandleFunc("/positions", func(w http.ResponseWriter, _ *http.Request) {
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

			_, err = client.ListPositions(context.Background(), "")
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

func TestClient_GetPosition_Error(t *testing.T) {
	t.Parallel()

	const ticket int64 = 999777

	mux := http.NewServeMux()
	mux.HandleFunc("/positions/", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)

		if err := json.NewEncoder(w).Encode(APIError{Error: "no such position"}); err != nil {
			t.Errorf("encode error body: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	_, err = client.GetPosition(context.Background(), ticket)
	if !errors.Is(err, aichteeteapee.ErrNotFound) {
		t.Fatalf("got err %v, want errors.Is match for %v", err, aichteeteapee.ErrNotFound)
	}

	if !strings.Contains(err.Error(), fmt.Sprintf("%d", ticket)) {
		t.Fatalf("error %q does not carry ticket context %d", err.Error(), ticket)
	}
}

func TestClient_UpdatePosition_Error(t *testing.T) {
	t.Parallel()

	const ticket int64 = 999888

	mux := http.NewServeMux()
	mux.HandleFunc("/positions/", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusConflict)

		if err := json.NewEncoder(w).Encode(APIError{Error: "position locked"}); err != nil {
			t.Errorf("encode error body: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	_, err = client.UpdatePosition(context.Background(), ticket, &UpdatePositionRequest{TP: 1})
	if !errors.Is(err, aichteeteapee.ErrConflict) {
		t.Fatalf("got err %v, want errors.Is match for %v", err, aichteeteapee.ErrConflict)
	}

	if !strings.Contains(err.Error(), fmt.Sprintf("%d", ticket)) {
		t.Fatalf("error %q does not carry ticket context %d", err.Error(), ticket)
	}
}

func TestClient_ClosePosition_Error(t *testing.T) {
	t.Parallel()

	const ticket int64 = 999999

	mux := http.NewServeMux()
	mux.HandleFunc("/positions/", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusUnprocessableEntity)

		if err := json.NewEncoder(w).Encode(APIError{Error: "cannot close"}); err != nil {
			t.Errorf("encode error body: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	_, err = client.ClosePosition(context.Background(), ticket, nil)
	if !errors.Is(err, aichteeteapee.ErrUnprocessableEntity) {
		t.Fatalf("got err %v, want errors.Is match for %v", err, aichteeteapee.ErrUnprocessableEntity)
	}

	if !strings.Contains(err.Error(), fmt.Sprintf("%d", ticket)) {
		t.Fatalf("error %q does not carry ticket context %d", err.Error(), ticket)
	}
}
