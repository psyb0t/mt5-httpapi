package mt5httpapi

import (
	"context"
	"net/http"
	"net/url"
	"strconv"

	"github.com/psyb0t/ctxerrors"
)

func (c *Client) ListSymbols(ctx context.Context, group string) ([]string, error) {
	var query url.Values
	if group != "" {
		query = url.Values{"group": []string{group}}
	}

	out := []string{}
	if err := c.do(ctx, http.MethodGet, "/symbols", query, nil, &out); err != nil {
		return nil, ctxerrors.Wrap(err, "list symbols")
	}

	return out, nil
}

func (c *Client) GetSymbol(ctx context.Context, symbol string) (*Symbol, error) {
	out := &Symbol{}
	if err := c.do(ctx, http.MethodGet, "/symbols/"+symbol, nil, nil, out); err != nil {
		return nil, ctxerrors.Wrapf(err, "get symbol %s", symbol)
	}

	return out, nil
}

func (c *Client) GetTick(ctx context.Context, symbol string) (*Tick, error) {
	out := &Tick{}
	if err := c.do(ctx, http.MethodGet, "/symbols/"+symbol+"/tick", nil, nil, out); err != nil {
		return nil, ctxerrors.Wrapf(err, "get tick %s", symbol)
	}

	return out, nil
}

func (c *Client) GetRates(
	ctx context.Context,
	symbol string,
	q RatesQuery,
) ([]Rate, error) {
	query := url.Values{}
	if q.Timeframe != "" {
		query.Set("timeframe", q.Timeframe)
	}

	if q.Count > 0 {
		query.Set("count", strconv.Itoa(q.Count))
	}

	if q.From > 0 {
		query.Set("from", strconv.FormatInt(q.From, 10))
	}

	if q.To > 0 {
		query.Set("to", strconv.FormatInt(q.To, 10))
	}

	out := []Rate{}
	if err := c.do(ctx, http.MethodGet, "/symbols/"+symbol+"/rates", query, nil, &out); err != nil {
		return nil, ctxerrors.Wrapf(err, "get rates %s", symbol)
	}

	return out, nil
}

func (c *Client) GetTicks(
	ctx context.Context,
	symbol string,
	q TicksQuery,
) ([]Tick, error) {
	query := url.Values{}
	if q.Count > 0 {
		query.Set("count", strconv.Itoa(q.Count))
	}

	if q.From > 0 {
		query.Set("from", strconv.FormatInt(q.From, 10))
	}

	if q.To > 0 {
		query.Set("to", strconv.FormatInt(q.To, 10))
	}

	if q.Flags != "" {
		query.Set("flags", string(q.Flags))
	}

	out := []Tick{}
	if err := c.do(ctx, http.MethodGet, "/symbols/"+symbol+"/ticks", query, nil, &out); err != nil {
		return nil, ctxerrors.Wrapf(err, "get ticks %s", symbol)
	}

	return out, nil
}
