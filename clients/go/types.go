package mt5httpapi

type APIError struct {
	Error string `json:"error"`
}

type PingResponse struct {
	Status string `json:"status"`
}

type LastError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

type SuccessResponse struct {
	Success bool   `json:"success"`
	Error   string `json:"error,omitempty"`
}

type Terminal struct {
	CommunityAccount     bool   `json:"community_account"`
	CommunityConnection  bool   `json:"community_connection"`
	Connected            bool   `json:"connected"`
	DLLsAllowed          bool   `json:"dlls_allowed"`
	TradeAllowed         bool   `json:"trade_allowed"`
	TradeAPIDisabled     bool   `json:"tradeapi_disabled"`
	EmailEnabled         bool   `json:"email_enabled"`
	FTPEnabled           bool   `json:"ftp_enabled"`
	NotificationsEnabled bool   `json:"notifications_enabled"`
	MQID                 bool   `json:"mqid"`
	Build                int    `json:"build"`
	MaxBars              int    `json:"maxbars"`
	CodePage             int    `json:"codepage"`
	PingLast             int    `json:"ping_last"`
	CommunityBalance     float64 `json:"community_balance"`
	Retransmission       float64 `json:"retransmission"`
	Company              string `json:"company"`
	Name                 string `json:"name"`
	Language             string `json:"language"`
	Path                 string `json:"path"`
	DataPath             string `json:"data_path"`
	CommonDataPath       string `json:"commondata_path"`
}

type Account struct {
	Login         int     `json:"login"`
	TradeMode     int     `json:"trade_mode"`
	Leverage      int     `json:"leverage"`
	LimitOrders   int     `json:"limit_orders"`
	MarginSOMode  int     `json:"margin_so_mode"`
	TradeAllowed  bool    `json:"trade_allowed"`
	TradeExpert   bool    `json:"trade_expert"`
	MarginMode    int     `json:"margin_mode"`
	CurrencyDigit int     `json:"currency_digits"`
	FIFOClose     bool    `json:"fifo_close"`
	Balance       float64 `json:"balance"`
	Credit        float64 `json:"credit"`
	Profit        float64 `json:"profit"`
	Equity        float64 `json:"equity"`
	Margin        float64 `json:"margin"`
	MarginFree    float64 `json:"margin_free"`
	MarginLevel   float64 `json:"margin_level"`
	MarginSOCall  float64 `json:"margin_so_call"`
	MarginSOSO    float64 `json:"margin_so_so"`
	MarginInitial float64 `json:"margin_initial"`
	MarginMaint   float64 `json:"margin_maintenance"`
	Assets        float64 `json:"assets"`
	Liabilities   float64 `json:"liabilities"`
	CommissionBlk float64 `json:"commission_blocked"`
	Name          string  `json:"name"`
	Server        string  `json:"server"`
	Currency      string  `json:"currency"`
	Company       string  `json:"company"`
}

type Symbol struct {
	Custom             bool    `json:"custom"`
	Chart              int     `json:"chart_mode"`
	Select             bool    `json:"select"`
	Visible            bool    `json:"visible"`
	SessionDeals       int     `json:"session_deals"`
	SessionBuyOrders   int     `json:"session_buy_orders"`
	SessionSellOrders  int     `json:"session_sell_orders"`
	Volume             float64 `json:"volume"`
	VolumeHigh         float64 `json:"volumehigh"`
	VolumeLow          float64 `json:"volumelow"`
	Time               int64   `json:"time"`
	Digits             int     `json:"digits"`
	Spread             int     `json:"spread"`
	SpreadFloat        bool    `json:"spread_float"`
	TicksBookDepth     int     `json:"ticks_bookdepth"`
	TradeCalcMode      int     `json:"trade_calc_mode"`
	TradeMode          int     `json:"trade_mode"`
	StartTime          int64   `json:"start_time"`
	ExpirationTime     int64   `json:"expiration_time"`
	TradeStopsLevel    int     `json:"trade_stops_level"`
	TradeFreezeLevel   int     `json:"trade_freeze_level"`
	TradeExeMode       int     `json:"trade_exemode"`
	SwapMode           int     `json:"swap_mode"`
	SwapRollover3Days  int     `json:"swap_rollover3days"`
	MarginHedgedUse    bool    `json:"margin_hedged_use_leg"`
	ExpirationMode     int     `json:"expiration_mode"`
	FillingMode        int     `json:"filling_mode"`
	OrderMode          int     `json:"order_mode"`
	OrderGTCMode       int     `json:"order_gtc_mode"`
	OptionMode         int     `json:"option_mode"`
	OptionRight        int     `json:"option_right"`
	Bid                float64 `json:"bid"`
	BidHigh            float64 `json:"bidhigh"`
	BidLow             float64 `json:"bidlow"`
	Ask                float64 `json:"ask"`
	AskHigh            float64 `json:"askhigh"`
	AskLow             float64 `json:"asklow"`
	Last               float64 `json:"last"`
	LastHigh           float64 `json:"lasthigh"`
	LastLow            float64 `json:"lastlow"`
	VolumeReal         float64 `json:"volume_real"`
	VolumeHighReal     float64 `json:"volumehigh_real"`
	VolumeLowReal      float64 `json:"volumelow_real"`
	OptionStrike       float64 `json:"option_strike"`
	Point              float64 `json:"point"`
	TradeTickValue     float64 `json:"trade_tick_value"`
	TradeTickValProfit float64 `json:"trade_tick_value_profit"`
	TradeTickValLoss   float64 `json:"trade_tick_value_loss"`
	TradeTickSize      float64 `json:"trade_tick_size"`
	TradeContractSize  float64 `json:"trade_contract_size"`
	TradeAccrued       float64 `json:"trade_accrued_interest"`
	TradeFaceValue     float64 `json:"trade_face_value"`
	TradeLiquidityRate float64 `json:"trade_liquidity_rate"`
	VolumeMin          float64 `json:"volume_min"`
	VolumeMax          float64 `json:"volume_max"`
	VolumeStep         float64 `json:"volume_step"`
	VolumeLimit        float64 `json:"volume_limit"`
	SwapLong           float64 `json:"swap_long"`
	SwapShort          float64 `json:"swap_short"`
	MarginInitial      float64 `json:"margin_initial"`
	MarginMaintenance  float64 `json:"margin_maintenance"`
	SessionVolume      float64 `json:"session_volume"`
	SessionTurnover    float64 `json:"session_turnover"`
	SessionInterest    float64 `json:"session_interest"`
	SessionBuyVolume   float64 `json:"session_buy_orders_volume"`
	SessionSellVolume  float64 `json:"session_sell_orders_volume"`
	SessionOpen        float64 `json:"session_open"`
	SessionClose       float64 `json:"session_close"`
	SessionAW          float64 `json:"session_aw"`
	SessionPriceSettle float64 `json:"session_price_settlement"`
	SessionPriceLimMin float64 `json:"session_price_limit_min"`
	SessionPriceLimMax float64 `json:"session_price_limit_max"`
	MarginHedged       float64 `json:"margin_hedged"`
	PriceChange        float64 `json:"price_change"`
	PriceVolatility    float64 `json:"price_volatility"`
	PriceTheoretical   float64 `json:"price_theoretical"`
	PriceGreeksDelta   float64 `json:"price_greeks_delta"`
	PriceGreeksTheta   float64 `json:"price_greeks_theta"`
	PriceGreeksGamma   float64 `json:"price_greeks_gamma"`
	PriceGreeksVega    float64 `json:"price_greeks_vega"`
	PriceGreeksRho     float64 `json:"price_greeks_rho"`
	PriceGreeksOmega   float64 `json:"price_greeks_omega"`
	PriceSensitivity   float64 `json:"price_sensitivity"`
	Basis              string  `json:"basis"`
	Category           string  `json:"category"`
	CurrencyBase       string  `json:"currency_base"`
	CurrencyProfit     string  `json:"currency_profit"`
	CurrencyMargin     string  `json:"currency_margin"`
	Bank               string  `json:"bank"`
	Description        string  `json:"description"`
	Exchange           string  `json:"exchange"`
	Formula            string  `json:"formula"`
	ISIN               string  `json:"isin"`
	Name               string  `json:"name"`
	Page               string  `json:"page"`
	Path               string  `json:"path"`
}

type Tick struct {
	Time       int64   `json:"time"`
	Bid        float64 `json:"bid"`
	Ask        float64 `json:"ask"`
	Last       float64 `json:"last"`
	Volume     int64   `json:"volume"`
	TimeMsc    int64   `json:"time_msc"`
	Flags      int     `json:"flags"`
	VolumeReal float64 `json:"volume_real"`
}

type Rate struct {
	Time        int64   `json:"time"`
	Open        float64 `json:"open"`
	High        float64 `json:"high"`
	Low         float64 `json:"low"`
	Close       float64 `json:"close"`
	TickVolume  int64   `json:"tick_volume"`
	Spread      int     `json:"spread"`
	RealVolume  int64   `json:"real_volume"`
}

type Order struct {
	Ticket          int64   `json:"ticket"`
	TimeSetup       int64   `json:"time_setup"`
	TimeSetupMsc    int64   `json:"time_setup_msc"`
	TimeDone        int64   `json:"time_done"`
	TimeDoneMsc     int64   `json:"time_done_msc"`
	TimeExpiration  int64   `json:"time_expiration"`
	Type            int     `json:"type"`
	TypeTime        int     `json:"type_time"`
	TypeFilling     int     `json:"type_filling"`
	State           int     `json:"state"`
	Magic           int64   `json:"magic"`
	PositionID      int64   `json:"position_id"`
	PositionByID    int64   `json:"position_by_id"`
	Reason          int     `json:"reason"`
	VolumeInitial   float64 `json:"volume_initial"`
	VolumeCurrent   float64 `json:"volume_current"`
	PriceOpen       float64 `json:"price_open"`
	SL              float64 `json:"sl"`
	TP              float64 `json:"tp"`
	PriceCurrent    float64 `json:"price_current"`
	PriceStopLimit  float64 `json:"price_stoplimit"`
	Symbol          string  `json:"symbol"`
	Comment         string  `json:"comment"`
	ExternalID      string  `json:"external_id"`
}

type Position struct {
	Ticket       int64   `json:"ticket"`
	Time         int64   `json:"time"`
	TimeMsc      int64   `json:"time_msc"`
	TimeUpdate   int64   `json:"time_update"`
	TimeUpdMsc   int64   `json:"time_update_msc"`
	Type         int     `json:"type"`
	Magic        int64   `json:"magic"`
	Identifier   int64   `json:"identifier"`
	Reason       int     `json:"reason"`
	Volume       float64 `json:"volume"`
	PriceOpen    float64 `json:"price_open"`
	SL           float64 `json:"sl"`
	TP           float64 `json:"tp"`
	PriceCurrent float64 `json:"price_current"`
	Swap         float64 `json:"swap"`
	Profit       float64 `json:"profit"`
	Symbol       string  `json:"symbol"`
	Comment      string  `json:"comment"`
	ExternalID   string  `json:"external_id"`
}

type Deal struct {
	Ticket     int64   `json:"ticket"`
	Order      int64   `json:"order"`
	Time       int64   `json:"time"`
	TimeMsc    int64   `json:"time_msc"`
	Type       int     `json:"type"`
	Entry      int     `json:"entry"`
	Magic      int64   `json:"magic"`
	PositionID int64   `json:"position_id"`
	Reason     int     `json:"reason"`
	Volume     float64 `json:"volume"`
	Price      float64 `json:"price"`
	Commission float64 `json:"commission"`
	Swap       float64 `json:"swap"`
	Profit     float64 `json:"profit"`
	Fee        float64 `json:"fee"`
	SL         float64 `json:"sl"`
	TP         float64 `json:"tp"`
	Symbol     string  `json:"symbol"`
	Comment    string  `json:"comment"`
	ExternalID string  `json:"external_id"`
}

type TradeResult struct {
	Retcode      int     `json:"retcode"`
	Deal         int64   `json:"deal"`
	Order        int64   `json:"order"`
	Volume       float64 `json:"volume"`
	Price        float64 `json:"price"`
	Bid          float64 `json:"bid"`
	Ask          float64 `json:"ask"`
	Comment      string  `json:"comment"`
	RequestID    int64   `json:"request_id"`
	RetcodeExt   int     `json:"retcode_external"`
}

type CreateOrderRequest struct {
	Symbol      string  `json:"symbol"`
	Type        string  `json:"type"`
	Volume      float64 `json:"volume"`
	Price       float64 `json:"price,omitempty"`
	SL          float64 `json:"sl,omitempty"`
	TP          float64 `json:"tp,omitempty"`
	Deviation   int     `json:"deviation,omitempty"`
	Magic       int64   `json:"magic,omitempty"`
	Comment     string  `json:"comment,omitempty"`
	TypeFilling string  `json:"type_filling,omitempty"`
	TypeTime    string  `json:"type_time,omitempty"`
	Expiration  int64   `json:"expiration,omitempty"`
	StopLimit   float64 `json:"stoplimit,omitempty"`
}

type UpdateOrderRequest struct {
	Price    float64 `json:"price,omitempty"`
	SL       float64 `json:"sl,omitempty"`
	TP       float64 `json:"tp,omitempty"`
	TypeTime string  `json:"type_time,omitempty"`
}

type UpdatePositionRequest struct {
	SL float64 `json:"sl,omitempty"`
	TP float64 `json:"tp,omitempty"`
}

type ClosePositionRequest struct {
	Volume    float64 `json:"volume,omitempty"`
	Deviation int     `json:"deviation,omitempty"`
}

type RatesQuery struct {
	Timeframe string
	Count     int
}

type TicksQuery struct {
	Count int
}

type HistoryQuery struct {
	From int64
	To   int64
}
