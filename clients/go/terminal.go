package mt5httpapi

import (
	"context"
	"net/http"

	"github.com/psyb0t/ctxerrors"
)

func (c *Client) Ping(ctx context.Context) (*PingResponse, error) {
	out := &PingResponse{}
	if err := c.do(ctx, http.MethodGet, "/ping", nil, nil, out); err != nil {
		return nil, ctxerrors.Wrap(err, "ping")
	}

	return out, nil
}

func (c *Client) LastError(ctx context.Context) (*LastError, error) {
	out := &LastError{}
	if err := c.do(ctx, http.MethodGet, "/error", nil, nil, out); err != nil {
		return nil, ctxerrors.Wrap(err, "get last error")
	}

	return out, nil
}

func (c *Client) GetTerminal(ctx context.Context) (*Terminal, error) {
	out := &Terminal{}
	if err := c.do(ctx, http.MethodGet, "/terminal", nil, nil, out); err != nil {
		return nil, ctxerrors.Wrap(err, "get terminal")
	}

	return out, nil
}

func (c *Client) InitTerminal(ctx context.Context) error {
	if err := c.do(ctx, http.MethodPost, "/terminal/init", nil, nil, nil); err != nil {
		return ctxerrors.Wrap(err, "init terminal")
	}

	return nil
}

func (c *Client) ShutdownTerminal(ctx context.Context) error {
	if err := c.do(ctx, http.MethodPost, "/terminal/shutdown", nil, nil, nil); err != nil {
		return ctxerrors.Wrap(err, "shutdown terminal")
	}

	return nil
}

func (c *Client) RestartTerminal(ctx context.Context) error {
	if err := c.do(ctx, http.MethodPost, "/terminal/restart", nil, nil, nil); err != nil {
		return ctxerrors.Wrap(err, "restart terminal")
	}

	return nil
}
