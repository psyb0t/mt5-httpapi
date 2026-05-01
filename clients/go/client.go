package mt5httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/psyb0t/aichteeteapee"
	"github.com/psyb0t/ctxerrors"
)

const defaultTimeout = 30 * time.Second

type Config struct {
	BaseURL    string
	Token      string
	Timeout    time.Duration
	HTTPClient *http.Client
}

type Client struct {
	baseURL string
	token   string
	http    *http.Client
}

func New(cfg Config) (*Client, error) {
	if cfg.BaseURL == "" {
		return nil, ErrEmptyBaseURL
	}

	httpClient := cfg.HTTPClient
	if httpClient == nil {
		timeout := cfg.Timeout
		if timeout == 0 {
			timeout = defaultTimeout
		}

		httpClient = &http.Client{Timeout: timeout}
	}

	return &Client{
		baseURL: strings.TrimRight(cfg.BaseURL, "/"),
		token:   cfg.Token,
		http:    httpClient,
	}, nil
}

func (c *Client) do(
	ctx context.Context,
	method, path string,
	query url.Values,
	body, out any,
) error {
	endpoint := c.baseURL + path
	if len(query) > 0 {
		endpoint += "?" + query.Encode()
	}

	var reqBody io.Reader

	if body != nil {
		buf, err := json.Marshal(body)
		if err != nil {
			return ctxerrors.Wrap(err, "marshal request body")
		}

		reqBody = bytes.NewReader(buf)
	}

	req, err := http.NewRequestWithContext(ctx, method, endpoint, reqBody)
	if err != nil {
		return ctxerrors.Wrap(err, "build request")
	}

	if body != nil {
		req.Header.Set(
			aichteeteapee.HeaderNameContentType,
			aichteeteapee.ContentTypeJSON,
		)
	}

	if c.token != "" {
		req.Header.Set(
			aichteeteapee.HeaderNameAuthorization,
			aichteeteapee.AuthSchemeBearer+c.token,
		)
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return ctxerrors.Wrap(err, "execute request")
	}
	defer func() { _ = resp.Body.Close() }()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return ctxerrors.Wrap(err, "read response body")
	}

	if err := mapStatus(resp.StatusCode, respBody); err != nil {
		return err
	}

	if out == nil {
		return nil
	}

	if len(respBody) == 0 {
		return nil
	}

	if err := json.Unmarshal(respBody, out); err != nil {
		return ctxerrors.Wrapf(
			err,
			"decode response body: %s",
			truncate(string(respBody), 200),
		)
	}

	return nil
}

func mapStatus(status int, body []byte) error {
	if status >= 200 && status < 300 {
		return nil
	}

	msg := extractError(body)

	base := statusToError(status)
	if base == nil {
		return ctxerrors.Wrapf(
			aichteeteapee.ErrUnexpectedResponseStatus,
			"status=%d body=%s",
			status,
			truncate(msg, 200),
		)
	}

	if msg == "" {
		return ctxerrors.Wrapf(base, "status=%d", status)
	}

	return ctxerrors.Wrapf(base, "status=%d: %s", status, msg)
}

func statusToError(status int) error {
	switch status {
	case http.StatusBadRequest:
		return aichteeteapee.ErrBadRequest
	case http.StatusUnauthorized:
		return aichteeteapee.ErrUnauthorized
	case http.StatusForbidden:
		return aichteeteapee.ErrForbidden
	case http.StatusNotFound:
		return aichteeteapee.ErrNotFound
	case http.StatusMethodNotAllowed:
		return aichteeteapee.ErrMethodNotAllowed
	case http.StatusConflict:
		return aichteeteapee.ErrConflict
	case http.StatusUnprocessableEntity:
		return aichteeteapee.ErrUnprocessableEntity
	case http.StatusTooManyRequests:
		return aichteeteapee.ErrTooManyRequests
	case http.StatusInternalServerError:
		return aichteeteapee.ErrInternalServer
	case http.StatusBadGateway:
		return aichteeteapee.ErrBadGateway
	case http.StatusServiceUnavailable:
		return ErrNotInitialized
	case http.StatusGatewayTimeout:
		return aichteeteapee.ErrGatewayTimeout
	}

	return nil
}

func extractError(body []byte) string {
	if len(body) == 0 {
		return ""
	}

	var apiErr APIError
	if err := json.Unmarshal(body, &apiErr); err == nil && apiErr.Error != "" {
		return apiErr.Error
	}

	return truncate(string(body), 200)
}

func truncate(s string, max int) string {
	if len(s) <= max {
		return s
	}

	return s[:max] + "..."
}

// IsNotInitialized reports whether err indicates MT5 hasn't initialized yet (HTTP 503).
func IsNotInitialized(err error) bool {
	return errors.Is(err, ErrNotInitialized)
}

func intStr(v int64) string {
	return strconv.FormatInt(v, 10)
}
