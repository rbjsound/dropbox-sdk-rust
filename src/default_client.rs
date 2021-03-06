// Copyright (c) 2020 Dropbox, Inc.

//! The default HTTP client.
//!
//! Use this client if you're not particularly picky about implementation details, as the specific
//! implementation is not exposed, and may be changed in the future.
//!
//! If you have a need for a specific HTTP client implementation, or your program is already using
//! some HTTP client crate, you probably want to have this Dropbox SDK crate use it as well. To do
//! that, you should implement the traits in `crate::client_trait` for it and use it instead.
//!
//! This code (and its dependencies) are only built if you use the `default_client` Cargo feature.

use crate::Error;
use crate::client_trait::*;
use crate::common::NamespaceId;

const USER_AGENT: &str = concat!("Dropbox-APIv2-Rust/", env!("CARGO_PKG_VERSION"));

macro_rules! forward_request {
    ($self:ident, $inner:expr, $token:expr, $team_select:expr, $namespace_id: expr) => {
        fn request(
            &$self,
            endpoint: Endpoint,
            style: Style,
            function: &str,
            params: String,
            params_type: ParamsType,
            body: Option<&[u8]>,
            range_start: Option<u64>,
            range_end: Option<u64>,
        ) -> crate::Result<HttpRequestResultRaw> {
            $inner.request(endpoint, style, function, params, params_type, body, range_start,
                range_end, $token, $team_select, $namespace_id)
        }
    }
}

/// Default HTTP client using User authorization.
pub struct UserAuthDefaultClient {
    inner: UreqClient,
    token: String,
    namespace_id: Option<NamespaceId>,
}

impl UserAuthDefaultClient {
    /// Create a new client using the given OAuth2 token.
    pub fn new(token: String) -> Self {
        Self {
            inner: UreqClient::default(),
            token,
            namespace_id: None,
        }
    }

    /// Set a namespace_id as the path root for future requests.
    pub fn namespace_id(&mut self, namespace_id: Option<NamespaceId>) {
        self.namespace_id = namespace_id;
    }
}

impl HttpClient for UserAuthDefaultClient {
    forward_request! { self, self.inner, Some(&self.token), None, self.namespace_id.as_ref() }
}

impl UserAuthClient for UserAuthDefaultClient {}

/// Default HTTP client using Team authorization.
pub struct TeamAuthDefaultClient {
    inner: UreqClient,
    token: String,
    team_select: Option<TeamSelect>,
}

impl TeamAuthDefaultClient {
    /// Create a new client using the given OAuth2 token, with no user/admin context selected.
    pub fn new(token: String) -> Self {
        Self {
            inner: UreqClient::default(),
            token,
            team_select: None,
        }
    }

    /// Select a user or team context to operate in.
    pub fn select(&mut self, team_select: Option<TeamSelect>) {
        self.team_select = team_select;
    }
}

impl HttpClient for TeamAuthDefaultClient {
    forward_request! { self, self.inner, Some(&self.token), self.team_select.as_ref(), None }
}

impl TeamAuthClient for TeamAuthDefaultClient {}

/// Default HTTP client for unauthenticated API calls.
#[derive(Debug, Default)]
pub struct NoauthDefaultClient {
    inner: UreqClient,
}

impl HttpClient for NoauthDefaultClient {
    forward_request! { self, self.inner, None, None, None }
}

impl NoauthClient for NoauthDefaultClient {}

#[derive(Debug, Default)]
struct UreqClient {}

impl UreqClient {
    #[allow(clippy::too_many_arguments)]
    fn request(
        &self,
        endpoint: Endpoint,
        style: Style,
        function: &str,
        params: String,
        params_type: ParamsType,
        body: Option<&[u8]>,
        range_start: Option<u64>,
        range_end: Option<u64>,
        token: Option<&str>,
        team_select: Option<&TeamSelect>,
        namespace_id: Option<&NamespaceId>,
    ) -> crate::Result<HttpRequestResultRaw> {

        let url = endpoint.url().to_owned() + function;
        debug!("request for {:?}", url);

        let mut req = ureq::post(&url);
        req.set("User-Agent", USER_AGENT);

        if let Some(token) = token {
            req.set("Authorization", &format!("Bearer {}", token));
        }

        if let Some(team_select) = team_select {
            match team_select {
                TeamSelect::User(id) => { req.set("Dropbox-API-Select-User", id); }
                TeamSelect::Admin(id) => { req.set("Dropbox-API-Select-Admin", id); }
            }
        }

        if let Some(namespace_id) = namespace_id {
            let namespace_tag = format!(r#"{{".tag": "namespace_id", "namespace_id": "{}"}}"#, namespace_id);
            req.set("Dropbox-API-Path-Root", &namespace_tag);
        }

        match (range_start, range_end) {
            (Some(start), Some(end)) => { req.set("Range", &format!("bytes={}-{}", start, end)); }
            (Some(start), None) => { req.set("Range", &format!("bytes={}-", start)); }
            (None, Some(end)) => { req.set("Range", &format!("bytes=-{}", end)); }
            (None, None) => (),
        }

        // If the params are totally empty, don't send any arg header or body.
        let resp = if params.is_empty() {
            req.call()
        } else {
            match style {
                Style::Rpc => {
                    // Send params in the body.
                    req.set("Content-Type", params_type.content_type());
                    req.send_string(&params)
                }
                Style::Upload | Style::Download => {
                    // Send params in a header.
                    req.set("Dropbox-API-Arg", &params);
                    if style == Style::Upload {
                        req.set("Content-Type", "application/octet-stream");
                        if let Some(body) = body {
                            req.send_bytes(body)
                        } else {
                            req.send_bytes(&[])
                        }
                    } else {
                        assert!(body.is_none(), "body can only be set for Style::Upload request");
                        req.call()
                    }
                }
            }
        };

        if let Some(ref err) = resp.synthetic_error() {
            error!("request failed: {}", err);
            return Err(RequestError { inner: resp }.into());
        }

        if !resp.ok() {
            let code = resp.status();
            let status = resp.status_text().to_owned();
            let json = resp.into_string()?;
            return Err(Error::UnexpectedHttpError {
                code,
                status,
                json,
            });
        }

        match style {
            Style::Rpc | Style::Upload => {
                // Get the response from the body; return no body stream.
                let result_json = resp.into_string()?;
                Ok(HttpRequestResultRaw {
                    result_json,
                    content_length: None,
                    body: None,
                })
            }
            Style::Download => {
                // Get the response from a header; return the body stream.
                let result_json = resp.header("Dropbox-API-Result")
                    .ok_or(Error::UnexpectedResponse("missing Dropbox-API-Result header"))?
                    .to_owned();

                let content_length = match resp.header("Content-Length") {
                    Some(s) => Some(s.parse()
                        .map_err(|_| Error::UnexpectedResponse("invalid Content-Length header"))?),
                    None => None,
                };

                Ok(HttpRequestResultRaw {
                    result_json,
                    content_length,
                    body: Some(Box::new(resp.into_reader())),
                })
            }
        }
    }
}

/// Errors from the HTTP client encountered in the course of making a request.
#[derive(thiserror::Error, Debug)]
#[allow(clippy::large_enum_variant)] // it's always boxed
pub enum DefaultClientError {
    /// The HTTP client encountered invalid UTF-8 data.
    #[error("invalid UTF-8 string")]
    Utf8(#[from] std::string::FromUtf8Error),

    /// The HTTP client encountered some I/O error.
    #[error("I/O error: {0}")]
    IO(#[from] std::io::Error),

    /// Some other error from the HTTP client implementation.
    #[error(transparent)]
    Request(#[from] RequestError),
}

macro_rules! wrap_error {
    ($e:ty) => {
        impl From<$e> for crate::Error {
            fn from(e: $e) -> Self {
                Self::HttpClient(Box::new(DefaultClientError::from(e)))
            }
        }
    }
}

wrap_error!(std::io::Error);
wrap_error!(std::string::FromUtf8Error);
wrap_error!(RequestError);

/// Something went wrong making the request, or the server returned a response we didn't expect.
/// Use the `Display` or `Debug` impls to see more details.
/// Note that this type is intentionally vague about the details beyond these string
/// representations, to allow implementation changes in the future.
pub struct RequestError {
    // ureq returns errors via "synthetic" responses, which contain an error inside them. However,
    // ureq::Error isn't Clone, so we can't copy it out to return it. So instead, we wrap up the
    // entire synthetic response, and forward relevant trait impls to the error inside it.
    // When https://github.com/algesten/ureq/issues/126 is fixed we can remove these shenanigans.
    inner: ureq::Response,
}

impl std::fmt::Display for RequestError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        <ureq::Error as std::fmt::Display>::fmt(self.inner.synthetic_error().as_ref().unwrap(), f)
    }
}

impl std::fmt::Debug for RequestError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        <ureq::Error as std::fmt::Debug>::fmt(self.inner.synthetic_error().as_ref().unwrap(), f)
    }
}

impl std::error::Error for RequestError {
    fn cause(&self) -> Option<&dyn std::error::Error> {
        Some(self.inner.synthetic_error().as_ref().unwrap())
    }
}
