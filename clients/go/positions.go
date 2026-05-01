package mt5httpapi

import (
	"context"
	"net/http"
	"net/url"

	"github.com/psyb0t/ctxerrors"
)

func (c *Client) ListPositions(ctx context.Context, symbol string) ([]Position, error) {
	var query url.Values
	if symbol != "" {
		query = url.Values{"symbol": []string{symbol}}
	}

	out := []Position{}
	if err := c.do(ctx, http.MethodGet, "/positions", query, nil, &out); err != nil {
		return nil, ctxerrors.Wrap(err, "list positions")
	}

	return out, nil
}

func (c *Client) GetPosition(ctx context.Context, ticket int64) (*Position, error) {
	out := &Position{}
	if err := c.do(ctx, http.MethodGet, "/positions/"+intStr(ticket), nil, nil, out); err != nil {
		return nil, ctxerrors.Wrapf(err, "get position %d", ticket)
	}

	return out, nil
}

func (c *Client) UpdatePosition(
	ctx context.Context,
	ticket int64,
	req *UpdatePositionRequest,
) (*TradeResult, error) {
	if req == nil {
		return nil, ErrNilRequest
	}

	out := &TradeResult{}
	if err := c.do(ctx, http.MethodPut, "/positions/"+intStr(ticket), nil, req, out); err != nil {
		return nil, ctxerrors.Wrapf(err, "update position %d", ticket)
	}

	return out, nil
}

func (c *Client) ClosePosition(
	ctx context.Context,
	ticket int64,
	req *ClosePositionRequest,
) (*TradeResult, error) {
	out := &TradeResult{}

	var body any
	if req != nil {
		body = req
	}

	if err := c.do(ctx, http.MethodDelete, "/positions/"+intStr(ticket), nil, body, out); err != nil {
		return nil, ctxerrors.Wrapf(err, "close position %d", ticket)
	}

	return out, nil
}
