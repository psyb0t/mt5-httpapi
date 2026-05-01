package mt5httpapi

import (
	"context"
	"net/http"
	"net/url"
	"strconv"

	"github.com/psyb0t/ctxerrors"
)

func (c *Client) HistoryOrders(ctx context.Context, q HistoryQuery) ([]Order, error) {
	query := url.Values{
		"from": []string{strconv.FormatInt(q.From, 10)},
		"to":   []string{strconv.FormatInt(q.To, 10)},
	}

	out := []Order{}
	if err := c.do(ctx, http.MethodGet, "/history/orders", query, nil, &out); err != nil {
		return nil, ctxerrors.Wrap(err, "history orders")
	}

	return out, nil
}

func (c *Client) HistoryDeals(ctx context.Context, q HistoryQuery) ([]Deal, error) {
	query := url.Values{
		"from": []string{strconv.FormatInt(q.From, 10)},
		"to":   []string{strconv.FormatInt(q.To, 10)},
	}

	out := []Deal{}
	if err := c.do(ctx, http.MethodGet, "/history/deals", query, nil, &out); err != nil {
		return nil, ctxerrors.Wrap(err, "history deals")
	}

	return out, nil
}
