# Dropbox SDK for Rust

Rust bindings to the Dropbox APIv2, generated by Stone from the official spec.

The Stone SDK and Dropbox API spec used to generate the code are in the `stone`
and `dropbox-api-spec` submodules, respectively. Use `git submodule init` and
`git submodule update` to fetch them.

The generated code is checked in under `src/generated` in order to simplify
building. To regenrate or update it, run `./generate.sh dropbox-api-spec`.
Doing so requires a working Python environment and some dependencies. See the
Stone documentation for details.

## HTTP Client

To actually use the API calls, you need a HTTP client -- all functions take a
`&HttpClient` as their first argument.  This trait is located at
`dropbox_sdk::client_trait::HttpClient`. Implement this trait and pass it as
the client argument.

If you don't want to implement your own, this SDK comes with an optional
default client that uses Hyper and your system's native TLS library.  To use
it, build with the `hyper_client` feature flag, and then there will be a
`dropbox_sdk::hyper_client::HyperClient` type that you can use.  The default
Hyper client needs a Dropbox API token; how you get one is up to you and your
program.

## Feature Flags

If you only use a subset of the API, and you want to cut down on the compile
time, you can explicitly specify features corresponding to the namespaces you
need. For each namespace there is a corresponding feature `dbx_{whatever}`. The
set of features can be updated if needed using the `update_manifest.py` script.
An example that only needs the 'files' and 'users' namespaces:
```
[dependencies.dropbox-sdk]
version = "*"
default_features = false
features = ["dbx_files", "dbx_users"]
```

## Miscellaneous

Some implementation notes, limitations, and TODOs:
 * Stone allows structures to inherit from other structures and be polymorphic.
   Rust doesn't have these paradigms, so instead this SDK represents
   polymorphic parent structs as enums, and the inherited fields are put in all
   variants.  See `dropbox_sdk::files::Metadata` for an example.
 * This code does not use `serde_derive` and instead uses manually-emitted
   serialization code.  Previous work did attempt to use `serde_derive`, but
   the way the Dropbox API serializes unions containing structs (by collapsing
   their fields into the union) isn't supported by `serde_derive`.  It also
   took an extremely long time to compile (~30 minutes for release build) and
   huge (~190MB) .rlib files.  The hand-written code is more versatile,
   compiles faster, and produces a smaller binary, at the expense of making the
   generated code much larger.
 * Types with constraints (such as strings with patterns or min/max lengths, or
   integers with a range) do not check that the data being stored in them meets
   the constraints.
 * The generated tests are not exhaustive. For unions with more than one
   variant, the test generator currently just picks one. Ideally it would emit
   tests for all variants.

## Happy Dropboxing!
