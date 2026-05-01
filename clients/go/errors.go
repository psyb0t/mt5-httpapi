package mt5httpapi

import "errors"

var (
	ErrNotInitialized = errors.New("mt5 not initialized")
	ErrEmptyBaseURL   = errors.New("base URL is required")
	ErrNilRequest     = errors.New("request is nil")
)
