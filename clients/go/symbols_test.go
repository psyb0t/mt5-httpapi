package mt5httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"reflect"
	"strings"
	"testing"

	"github.com/psyb0t/aichteeteapee"
)

func symbolsFullSymbolJSON() map[string]any {
	return map[string]any{
		"custom":                     true,
		"chart_mode":                 107,
		"select":                     true,
		"visible":                    true,
		"session_deals":              128,
		"session_buy_orders":         135,
		"session_sell_orders":        142,
		"volume":                     121.25,
		"volumehigh":                 124.25,
		"volumelow":                  127.25,
		"time":                       170,
		"digits":                     177,
		"spread":                     184,
		"spread_float":               true,
		"ticks_bookdepth":            198,
		"trade_calc_mode":            205,
		"trade_mode":                 212,
		"start_time":                 219,
		"expiration_time":            226,
		"trade_stops_level":          233,
		"trade_freeze_level":         240,
		"trade_exemode":              247,
		"swap_mode":                  254,
		"swap_rollover3days":         261,
		"margin_hedged_use_leg":      true,
		"expiration_mode":            275,
		"filling_mode":               282,
		"order_mode":                 289,
		"order_gtc_mode":             296,
		"option_mode":                303,
		"option_right":               310,
		"bid":                        193.25,
		"bidhigh":                    196.25,
		"bidlow":                     199.25,
		"ask":                        202.25,
		"askhigh":                    205.25,
		"asklow":                     208.25,
		"last":                       211.25,
		"lasthigh":                   214.25,
		"lastlow":                    217.25,
		"volume_real":                220.25,
		"volumehigh_real":            223.25,
		"volumelow_real":             226.25,
		"option_strike":              229.25,
		"point":                      232.25,
		"trade_tick_value":           235.25,
		"trade_tick_value_profit":    238.25,
		"trade_tick_value_loss":      241.25,
		"trade_tick_size":            244.25,
		"trade_contract_size":        247.25,
		"trade_accrued_interest":     250.25,
		"trade_face_value":           253.25,
		"trade_liquidity_rate":       256.25,
		"volume_min":                 259.25,
		"volume_max":                 262.25,
		"volume_step":                265.25,
		"volume_limit":               268.25,
		"swap_long":                  271.25,
		"swap_short":                 274.25,
		"margin_initial":             277.25,
		"margin_maintenance":         280.25,
		"session_volume":             283.25,
		"session_turnover":           286.25,
		"session_interest":           289.25,
		"session_buy_orders_volume":  292.25,
		"session_sell_orders_volume": 295.25,
		"session_open":               298.25,
		"session_close":              301.25,
		"session_aw":                 304.25,
		"session_price_settlement":   307.25,
		"session_price_limit_min":    310.25,
		"session_price_limit_max":    313.25,
		"margin_hedged":              316.25,
		"price_change":               319.25,
		"price_volatility":           322.25,
		"price_theoretical":          325.25,
		"price_greeks_delta":         328.25,
		"price_greeks_theta":         331.25,
		"price_greeks_gamma":         334.25,
		"price_greeks_vega":          337.25,
		"price_greeks_rho":           340.25,
		"price_greeks_omega":         343.25,
		"price_sensitivity":          346.25,
		"basis":                      "test-basis-83",
		"category":                   "test-category-84",
		"currency_base":              "TESTUSD",
		"currency_profit":            "TESTEUR",
		"currency_margin":            "TESTGBP",
		"bank":                       "test-bank-88",
		"description":                "test-description-89",
		"exchange":                   "test-exchange-90",
		"formula":                    "test-formula-91",
		"isin":                       "test-isin-92",
		"name":                       "test-name-93",
		"page":                       "test-page-94",
		"path":                       "test-path-95",
	}
}

func symbolsFullSymbolWant() Symbol {
	return Symbol{
		Custom:             true,
		Chart:              107,
		Select:             true,
		Visible:            true,
		SessionDeals:       128,
		SessionBuyOrders:   135,
		SessionSellOrders:  142,
		Volume:             121.25,
		VolumeHigh:         124.25,
		VolumeLow:          127.25,
		Time:               170,
		Digits:             177,
		Spread:             184,
		SpreadFloat:        true,
		TicksBookDepth:     198,
		TradeCalcMode:      205,
		TradeMode:          212,
		StartTime:          219,
		ExpirationTime:     226,
		TradeStopsLevel:    233,
		TradeFreezeLevel:   240,
		TradeExeMode:       247,
		SwapMode:           254,
		SwapRollover3Days:  261,
		MarginHedgedUse:    true,
		ExpirationMode:     275,
		FillingMode:        282,
		OrderMode:          289,
		OrderGTCMode:       296,
		OptionMode:         303,
		OptionRight:        310,
		Bid:                193.25,
		BidHigh:            196.25,
		BidLow:             199.25,
		Ask:                202.25,
		AskHigh:            205.25,
		AskLow:             208.25,
		Last:               211.25,
		LastHigh:           214.25,
		LastLow:            217.25,
		VolumeReal:         220.25,
		VolumeHighReal:     223.25,
		VolumeLowReal:      226.25,
		OptionStrike:       229.25,
		Point:              232.25,
		TradeTickValue:     235.25,
		TradeTickValProfit: 238.25,
		TradeTickValLoss:   241.25,
		TradeTickSize:      244.25,
		TradeContractSize:  247.25,
		TradeAccrued:       250.25,
		TradeFaceValue:     253.25,
		TradeLiquidityRate: 256.25,
		VolumeMin:          259.25,
		VolumeMax:          262.25,
		VolumeStep:         265.25,
		VolumeLimit:        268.25,
		SwapLong:           271.25,
		SwapShort:          274.25,
		MarginInitial:      277.25,
		MarginMaintenance:  280.25,
		SessionVolume:      283.25,
		SessionTurnover:    286.25,
		SessionInterest:    289.25,
		SessionBuyVolume:   292.25,
		SessionSellVolume:  295.25,
		SessionOpen:        298.25,
		SessionClose:       301.25,
		SessionAW:          304.25,
		SessionPriceSettle: 307.25,
		SessionPriceLimMin: 310.25,
		SessionPriceLimMax: 313.25,
		MarginHedged:       316.25,
		PriceChange:        319.25,
		PriceVolatility:    322.25,
		PriceTheoretical:   325.25,
		PriceGreeksDelta:   328.25,
		PriceGreeksTheta:   331.25,
		PriceGreeksGamma:   334.25,
		PriceGreeksVega:    337.25,
		PriceGreeksRho:     340.25,
		PriceGreeksOmega:   343.25,
		PriceSensitivity:   346.25,
		Basis:              "test-basis-83",
		Category:           "test-category-84",
		CurrencyBase:       "TESTUSD",
		CurrencyProfit:     "TESTEUR",
		CurrencyMargin:     "TESTGBP",
		Bank:               "test-bank-88",
		Description:        "test-description-89",
		Exchange:           "test-exchange-90",
		Formula:            "test-formula-91",
		ISIN:               "test-isin-92",
		Name:               "test-name-93",
		Page:               "test-page-94",
		Path:               "test-path-95",
	}
}

func symbolsFullTickJSON() map[string]any {
	return map[string]any{
		"time":        100,
		"bid":         103.25,
		"ask":         106.25,
		"last":        109.25,
		"volume":      244,
		"time_msc":    135,
		"flags":       142,
		"volume_real": 121.25,
	}
}

func symbolsFullTickWant() Tick {
	return Tick{
		Time:       100,
		Bid:        103.25,
		Ask:        106.25,
		Last:       109.25,
		Volume:     244,
		TimeMsc:    135,
		Flags:      142,
		VolumeReal: 121.25,
	}
}

func symbolsFullRateJSON() map[string]any {
	return map[string]any{
		"time":        100,
		"open":        103.25,
		"high":        106.25,
		"low":         109.25,
		"close":       112.25,
		"tick_volume": 255,
		"spread":      142,
		"real_volume": 277,
	}
}

func symbolsFullRateWant() Rate {
	return Rate{
		Time:       100,
		Open:       103.25,
		High:       106.25,
		Low:        109.25,
		Close:      112.25,
		TickVolume: 255,
		Spread:     142,
		RealVolume: 277,
	}
}

func TestClient_ListSymbols(t *testing.T) {
	t.Parallel()

	testCases := []struct {
		name      string
		group     string
		wantQuery bool
	}{
		{name: "with group filter", group: "test-group-forex", wantQuery: true},
		{name: "without group filter", group: "", wantQuery: false},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			mux := http.NewServeMux()
			mux.HandleFunc("/symbols", func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodGet {
					t.Errorf("unexpected method: %s", r.Method)
				}

				values, hasGroup := r.URL.Query()["group"]
				if tc.wantQuery {
					if !hasGroup || len(values) != 1 || values[0] != tc.group {
						t.Errorf("unexpected group query: %v", r.URL.Query())
					}
				} else if hasGroup {
					t.Errorf("unexpected group query present: %v", values)
				}

				body := []string{"TESTUSD", "TESTEUR"}
				if err := json.NewEncoder(w).Encode(body); err != nil {
					t.Errorf("encode symbols response: %v", err)
				}
			})

			server := httptest.NewServer(mux)
			t.Cleanup(server.Close)

			client, err := New(Config{BaseURL: server.URL})
			if err != nil {
				t.Fatalf("create client: %v", err)
			}

			got, err := client.ListSymbols(context.Background(), tc.group)
			if err != nil {
				t.Fatalf("list symbols: %v", err)
			}

			want := []string{"TESTUSD", "TESTEUR"}
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("unexpected symbols:\ngot  %#v\nwant %#v", got, want)
			}
		})
	}
}

func TestClient_GetSymbol(t *testing.T) {
	t.Parallel()

	mux := http.NewServeMux()
	mux.HandleFunc("/symbols/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/symbols/TESTUSD" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}

		if r.Method != http.MethodGet {
			t.Errorf("unexpected method: %s", r.Method)
		}

		if err := json.NewEncoder(w).Encode(symbolsFullSymbolJSON()); err != nil {
			t.Errorf("encode symbol response: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	got, err := client.GetSymbol(context.Background(), "TESTUSD")
	if err != nil {
		t.Fatalf("get symbol: %v", err)
	}

	want := symbolsFullSymbolWant()
	if !reflect.DeepEqual(got, &want) {
		t.Fatalf("unexpected symbol:\ngot  %#v\nwant %#v", got, &want)
	}
}

func TestClient_GetTick(t *testing.T) {
	t.Parallel()

	mux := http.NewServeMux()
	mux.HandleFunc("/symbols/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/symbols/TESTUSD/tick" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}

		if r.Method != http.MethodGet {
			t.Errorf("unexpected method: %s", r.Method)
		}

		if err := json.NewEncoder(w).Encode(symbolsFullTickJSON()); err != nil {
			t.Errorf("encode tick response: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	got, err := client.GetTick(context.Background(), "TESTUSD")
	if err != nil {
		t.Fatalf("get tick: %v", err)
	}

	want := symbolsFullTickWant()
	if !reflect.DeepEqual(got, &want) {
		t.Fatalf("unexpected tick:\ngot  %#v\nwant %#v", got, &want)
	}
}

func TestClient_GetRates_QueryParams(t *testing.T) {
	t.Parallel()

	testCases := []struct {
		name      string
		query     RatesQuery
		wantQuery url.Values
	}{
		{
			name:      "count mode, no anchor",
			query:     RatesQuery{Timeframe: "M1", Count: 10},
			wantQuery: url.Values{"timeframe": {"M1"}, "count": {"10"}},
		},
		{
			name:      "count mode, negative count with anchor",
			query:     RatesQuery{Timeframe: "H1", Count: -5, From: 1700000000},
			wantQuery: url.Values{"timeframe": {"H1"}, "count": {"-5"}, "from": {"1700000000"}},
		},
		{
			name:      "range mode",
			query:     RatesQuery{Timeframe: "M5", From: 1700000000, To: 1700003600},
			wantQuery: url.Values{"timeframe": {"M5"}, "from": {"1700000000"}, "to": {"1700003600"}},
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			mux := http.NewServeMux()
			mux.HandleFunc("/symbols/", func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != "/symbols/TESTUSD/rates" {
					t.Errorf("unexpected path: %s", r.URL.Path)
				}

				if !reflect.DeepEqual(r.URL.Query(), tc.wantQuery) {
					t.Errorf("unexpected query:\ngot  %#v\nwant %#v", r.URL.Query(), tc.wantQuery)
				}

				body := []map[string]any{symbolsFullRateJSON()}
				if err := json.NewEncoder(w).Encode(body); err != nil {
					t.Errorf("encode rates response: %v", err)
				}
			})

			server := httptest.NewServer(mux)
			t.Cleanup(server.Close)

			client, err := New(Config{BaseURL: server.URL})
			if err != nil {
				t.Fatalf("create client: %v", err)
			}

			got, err := client.GetRates(context.Background(), "TESTUSD", tc.query)
			if err != nil {
				t.Fatalf("get rates: %v", err)
			}

			want := []Rate{symbolsFullRateWant()}
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("unexpected rates:\ngot  %#v\nwant %#v", got, want)
			}
		})
	}
}

func TestClient_GetRates_ValidationErrors(t *testing.T) {
	t.Parallel()

	testCases := []struct {
		name          string
		query         RatesQuery
		wantSubstring string
	}{
		{
			name:          "count and to mutually exclusive",
			query:         RatesQuery{To: 1700000000, Count: 10},
			wantSubstring: "mutually exclusive",
		},
		{
			name:          "to requires from",
			query:         RatesQuery{To: 1700000000},
			wantSubstring: "to requires from",
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			calls := 0
			mux := http.NewServeMux()
			mux.HandleFunc("/symbols/", func(w http.ResponseWriter, _ *http.Request) {
				calls++
			})

			server := httptest.NewServer(mux)
			t.Cleanup(server.Close)

			client, err := New(Config{BaseURL: server.URL})
			if err != nil {
				t.Fatalf("create client: %v", err)
			}

			_, err = client.GetRates(context.Background(), "TESTUSD", tc.query)
			if err == nil {
				t.Fatalf("expected validation error, got nil")
			}

			if !strings.Contains(err.Error(), tc.wantSubstring) {
				t.Fatalf("error %q does not contain %q", err.Error(), tc.wantSubstring)
			}

			if calls != 0 {
				t.Fatalf("expected no HTTP call, got %d", calls)
			}
		})
	}
}

func TestClient_GetRatesTA(t *testing.T) {
	t.Parallel()

	const wantTA = `{"rsi14":[10,20,30]}`

	mux := http.NewServeMux()
	mux.HandleFunc("/symbols/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/symbols/TESTUSD/rates/ta" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}

		if r.Method != http.MethodPost {
			t.Errorf("unexpected method: %s", r.Method)
		}

		wantQuery := url.Values{"timeframe": {"M1"}, "count": {"10"}}
		if !reflect.DeepEqual(r.URL.Query(), wantQuery) {
			t.Errorf("unexpected query:\ngot  %#v\nwant %#v", r.URL.Query(), wantQuery)
		}

		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Errorf("decode request body: %v", err)
			return
		}

		wantIndicators := map[string]any{"rsi14": true}
		if !reflect.DeepEqual(body["indicators"], wantIndicators) {
			t.Errorf("unexpected indicators:\ngot  %#v\nwant %#v", body["indicators"], wantIndicators)
		}

		if got, want := body["recentBars"], 5.0; got != want {
			t.Errorf("unexpected recentBars: got %#v, want %v", got, want)
		}

		respBody := fmt.Sprintf(
			`{"symbol":"TESTUSD","timeframe":"M1","bars":[%s],"ta":%s}`,
			mustMarshal(t, symbolsFullRateJSON()),
			wantTA,
		)

		if _, err := w.Write([]byte(respBody)); err != nil {
			t.Errorf("write response: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	q := RatesTAQuery{
		Timeframe:  "M1",
		Count:      10,
		Indicators: map[string]any{"rsi14": true},
		RecentBars: 5,
	}

	got, err := client.GetRatesTA(context.Background(), "TESTUSD", q)
	if err != nil {
		t.Fatalf("get rates+ta: %v", err)
	}

	if got.Symbol != "TESTUSD" || got.Timeframe != "M1" {
		t.Fatalf("unexpected symbol/timeframe: %#v", got)
	}

	wantBars := []Rate{symbolsFullRateWant()}
	if !reflect.DeepEqual(got.Bars, wantBars) {
		t.Fatalf("unexpected bars:\ngot  %#v\nwant %#v", got.Bars, wantBars)
	}

	var gotTA, wantTAAny any
	if err := json.Unmarshal(got.TA, &gotTA); err != nil {
		t.Fatalf("unmarshal got TA: %v", err)
	}

	if err := json.Unmarshal([]byte(wantTA), &wantTAAny); err != nil {
		t.Fatalf("unmarshal want TA: %v", err)
	}

	if !reflect.DeepEqual(gotTA, wantTAAny) {
		t.Fatalf("unexpected TA:\ngot  %#v\nwant %#v", gotTA, wantTAAny)
	}
}

func mustMarshal(t *testing.T, v any) string {
	t.Helper()

	buf, err := json.Marshal(v)
	if err != nil {
		t.Fatalf("marshal test fixture: %v", err)
	}

	return string(buf)
}

func TestClient_GetRatesTA_ValidationErrors(t *testing.T) {
	t.Parallel()

	testCases := []struct {
		name          string
		query         RatesTAQuery
		wantSubstring string
	}{
		{
			name:          "empty indicators",
			query:         RatesTAQuery{Timeframe: "M1", Count: 10},
			wantSubstring: "indicators must not be empty",
		},
		{
			name: "count and to mutually exclusive",
			query: RatesTAQuery{
				Timeframe: "M1", Count: 10, To: 1700000000,
				Indicators: map[string]any{"rsi14": true},
			},
			wantSubstring: "mutually exclusive",
		},
		{
			name: "to requires from",
			query: RatesTAQuery{
				Timeframe: "M1", To: 1700000000,
				Indicators: map[string]any{"rsi14": true},
			},
			wantSubstring: "to requires from",
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			calls := 0
			mux := http.NewServeMux()
			mux.HandleFunc("/symbols/", func(w http.ResponseWriter, _ *http.Request) {
				calls++
			})

			server := httptest.NewServer(mux)
			t.Cleanup(server.Close)

			client, err := New(Config{BaseURL: server.URL})
			if err != nil {
				t.Fatalf("create client: %v", err)
			}

			_, err = client.GetRatesTA(context.Background(), "TESTUSD", tc.query)
			if err == nil {
				t.Fatalf("expected validation error, got nil")
			}

			if !strings.Contains(err.Error(), tc.wantSubstring) {
				t.Fatalf("error %q does not contain %q", err.Error(), tc.wantSubstring)
			}

			if calls != 0 {
				t.Fatalf("expected no HTTP call, got %d", calls)
			}
		})
	}
}

func TestClient_GetTicks_QueryParams(t *testing.T) {
	t.Parallel()

	testCases := []struct {
		name      string
		query     TicksQuery
		wantQuery url.Values
	}{
		{
			name:      "count mode with flags",
			query:     TicksQuery{Count: 20, Flags: TickFlagInfo},
			wantQuery: url.Values{"count": {"20"}, "flags": {"INFO"}},
		},
		{
			name:      "range mode",
			query:     TicksQuery{From: 1700000000, To: 1700003600},
			wantQuery: url.Values{"from": {"1700000000"}, "to": {"1700003600"}},
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			mux := http.NewServeMux()
			mux.HandleFunc("/symbols/", func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != "/symbols/TESTUSD/ticks" {
					t.Errorf("unexpected path: %s", r.URL.Path)
				}

				if !reflect.DeepEqual(r.URL.Query(), tc.wantQuery) {
					t.Errorf("unexpected query:\ngot  %#v\nwant %#v", r.URL.Query(), tc.wantQuery)
				}

				body := []map[string]any{symbolsFullTickJSON()}
				if err := json.NewEncoder(w).Encode(body); err != nil {
					t.Errorf("encode ticks response: %v", err)
				}
			})

			server := httptest.NewServer(mux)
			t.Cleanup(server.Close)

			client, err := New(Config{BaseURL: server.URL})
			if err != nil {
				t.Fatalf("create client: %v", err)
			}

			got, err := client.GetTicks(context.Background(), "TESTUSD", tc.query)
			if err != nil {
				t.Fatalf("get ticks: %v", err)
			}

			want := []Tick{symbolsFullTickWant()}
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("unexpected ticks:\ngot  %#v\nwant %#v", got, want)
			}
		})
	}
}

func TestClient_GetTicks_ValidationErrors(t *testing.T) {
	t.Parallel()

	testCases := []struct {
		name          string
		query         TicksQuery
		wantSubstring string
	}{
		{
			name:          "count and to mutually exclusive",
			query:         TicksQuery{To: 1700000000, Count: 10},
			wantSubstring: "mutually exclusive",
		},
		{
			name:          "to requires from",
			query:         TicksQuery{To: 1700000000},
			wantSubstring: "to requires from",
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			calls := 0
			mux := http.NewServeMux()
			mux.HandleFunc("/symbols/", func(w http.ResponseWriter, _ *http.Request) {
				calls++
			})

			server := httptest.NewServer(mux)
			t.Cleanup(server.Close)

			client, err := New(Config{BaseURL: server.URL})
			if err != nil {
				t.Fatalf("create client: %v", err)
			}

			_, err = client.GetTicks(context.Background(), "TESTUSD", tc.query)
			if err == nil {
				t.Fatalf("expected validation error, got nil")
			}

			if !strings.Contains(err.Error(), tc.wantSubstring) {
				t.Fatalf("error %q does not contain %q", err.Error(), tc.wantSubstring)
			}

			if calls != 0 {
				t.Fatalf("expected no HTTP call, got %d", calls)
			}
		})
	}
}

func TestClient_Symbols_ErrorMapping(t *testing.T) {
	t.Parallel()

	testCases := []struct {
		name       string
		status     int
		apiMessage string
		wantErr    error
	}{
		{
			name: "bad request", status: http.StatusBadRequest,
			apiMessage: "invalid group", wantErr: aichteeteapee.ErrBadRequest,
		},
		{
			name: "not found", status: http.StatusNotFound,
			apiMessage: "symbols missing", wantErr: aichteeteapee.ErrNotFound,
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
			mux.HandleFunc("/symbols", func(w http.ResponseWriter, _ *http.Request) {
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

			_, err = client.ListSymbols(context.Background(), "")
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

func TestClient_GetSymbol_Error(t *testing.T) {
	t.Parallel()

	mux := http.NewServeMux()
	mux.HandleFunc("/symbols/", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)

		if err := json.NewEncoder(w).Encode(APIError{Error: "no such symbol"}); err != nil {
			t.Errorf("encode error body: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	_, err = client.GetSymbol(context.Background(), "TESTUSD")
	if !errors.Is(err, aichteeteapee.ErrNotFound) {
		t.Fatalf("got err %v, want errors.Is match for %v", err, aichteeteapee.ErrNotFound)
	}

	if !strings.Contains(err.Error(), "TESTUSD") {
		t.Fatalf("error %q does not carry symbol context", err.Error())
	}
}

func TestClient_GetTick_Error(t *testing.T) {
	t.Parallel()

	mux := http.NewServeMux()
	mux.HandleFunc("/symbols/", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)

		if err := json.NewEncoder(w).Encode(APIError{Error: "no such symbol"}); err != nil {
			t.Errorf("encode error body: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	_, err = client.GetTick(context.Background(), "TESTUSD")
	if !errors.Is(err, aichteeteapee.ErrNotFound) {
		t.Fatalf("got err %v, want errors.Is match for %v", err, aichteeteapee.ErrNotFound)
	}

	if !strings.Contains(err.Error(), "TESTUSD") {
		t.Fatalf("error %q does not carry symbol context", err.Error())
	}
}

func TestClient_GetRates_Error(t *testing.T) {
	t.Parallel()

	mux := http.NewServeMux()
	mux.HandleFunc("/symbols/", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)

		if err := json.NewEncoder(w).Encode(APIError{Error: "boom"}); err != nil {
			t.Errorf("encode error body: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	_, err = client.GetRates(context.Background(), "TESTUSD", RatesQuery{Count: 1})
	if !errors.Is(err, aichteeteapee.ErrInternalServer) {
		t.Fatalf("got err %v, want errors.Is match for %v", err, aichteeteapee.ErrInternalServer)
	}

	if !strings.Contains(err.Error(), "TESTUSD") {
		t.Fatalf("error %q does not carry symbol context", err.Error())
	}
}

func TestClient_GetRatesTA_Error(t *testing.T) {
	t.Parallel()

	mux := http.NewServeMux()
	mux.HandleFunc("/symbols/", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusBadGateway)

		if err := json.NewEncoder(w).Encode(APIError{Error: "upstream ta failure"}); err != nil {
			t.Errorf("encode error body: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	q := RatesTAQuery{Count: 1, Indicators: map[string]any{"rsi14": true}}

	_, err = client.GetRatesTA(context.Background(), "TESTUSD", q)
	if !errors.Is(err, aichteeteapee.ErrBadGateway) {
		t.Fatalf("got err %v, want errors.Is match for %v", err, aichteeteapee.ErrBadGateway)
	}

	if !strings.Contains(err.Error(), "TESTUSD") {
		t.Fatalf("error %q does not carry symbol context", err.Error())
	}
}

func TestClient_GetTicks_Error(t *testing.T) {
	t.Parallel()

	mux := http.NewServeMux()
	mux.HandleFunc("/symbols/", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)

		if err := json.NewEncoder(w).Encode(APIError{Error: "no such symbol"}); err != nil {
			t.Errorf("encode error body: %v", err)
		}
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	_, err = client.GetTicks(context.Background(), "TESTUSD", TicksQuery{Count: 1})
	if !errors.Is(err, aichteeteapee.ErrNotFound) {
		t.Fatalf("got err %v, want errors.Is match for %v", err, aichteeteapee.ErrNotFound)
	}

	if !strings.Contains(err.Error(), "TESTUSD") {
		t.Fatalf("error %q does not carry symbol context", err.Error())
	}
}
