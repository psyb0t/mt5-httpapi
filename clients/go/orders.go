package mt5httpapi

import (
	"context"
	"net/http"
	"net/url"

	"github.com/psyb0t/ctxerrors"
)

func (c *Client) ListOrders(ctx context.Context, symbol string) ([]Order, error) {
	var query url.Values
	if symbol != "" {
		query = url.Values{"symbol": []string{symbol}}
	}

	out := []Order{}
	if err := c.do(ctx, http.MethodGet, "/orders", query, nil, &out); err != nil {
		return nil, ctxerrors.Wrap(err, "list orders")
	}

	return out, nil
}

func (c *Client) CreateOrder(ctx context.Context, req *CreateOrderRequest) (*TradeResult, error) {
	if req == nil {
		return nil, ErrNilRequest
	}

	out := &TradeResult{}
	if err := c.do(ctx, http.MethodPost, "/orders", nil, req, out); err != nil {
		return nil, ctxerrors.Wrap(err, "create order")
	}

	return out, nil
}

func (c *Client) GetOrder(ctx context.Context, ticket int64) (*Order, error) {
	out := &Order{}
	if err := c.do(ctx, http.MethodGet, "/orders/"+intStr(ticket), nil, nil, out); err != nil {
		return nil, ctxerrors.Wrapf(err, "get order %d", ticket)
	}

	return out, nil
}

func (c *Client) UpdateOrder(
	ctx context.Context,
	ticket int64,
	req *UpdateOrderRequest,
) (*TradeResult, error) {
	if req == nil {
		return nil, ErrNilRequest
	}

	out := &TradeResult{}
	if err := c.do(ctx, http.MethodPut, "/orders/"+intStr(ticket), nil, req, out); err != nil {
		return nil, ctxerrors.Wrapf(err, "update order %d", ticket)
	}

	return out, nil
}

func (c *Client) CancelOrder(ctx context.Context, ticket int64) (*TradeResult, error) {
	out := &TradeResult{}
	if err := c.do(ctx, http.MethodDelete, "/orders/"+intStr(ticket), nil, nil, out); err != nil {
		return nil, ctxerrors.Wrapf(err, "cancel order %d", ticket)
	}

	return out, nil
}
