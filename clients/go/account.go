package mt5httpapi

import (
	"context"
	"net/http"

	"github.com/psyb0t/ctxerrors"
)

func (c *Client) GetAccount(ctx context.Context) (*Account, error) {
	out := &Account{}
	if err := c.do(ctx, http.MethodGet, "/account", nil, nil, out); err != nil {
		return nil, ctxerrors.Wrap(err, "get account")
	}

	return out, nil
}
