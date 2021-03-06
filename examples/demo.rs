#![deny(rust_2018_idioms)]

//! This example illustrates a few basic Dropbox API operations: getting an OAuth2 token, listing
//! the contents of a folder recursively, and fetching a file given its path.

use dropbox_sdk::{files, UserAuthClient};
use dropbox_sdk::oauth2::{oauth2_token_from_authorization_code, Oauth2AuthorizeUrlBuilder,
    Oauth2Type};
use dropbox_sdk::default_client::{NoauthDefaultClient, UserAuthDefaultClient};

use std::collections::VecDeque;
use std::env;
use std::io::{self, Read, Write};

enum Operation {
    Usage,
    List,
    Download { path: String },
}

fn parse_args() -> Operation {
    match std::env::args().nth(1).as_deref() {
        None | Some("--help") | Some("-h") => Operation::Usage,
        Some("--list") => Operation::List,
        Some(path) if path.starts_with('/') => Operation::Download { path: path.to_owned() },
        Some(bogus) => {
            eprintln!("Unrecognized option {:?}", bogus);
            eprintln!();
            Operation::Usage
        }
    }
}

fn prompt(msg: &str) -> String {
    eprint!("{}: ", msg);
    io::stderr().flush().unwrap();
    let mut input = String::new();
    io::stdin().read_line(&mut input).unwrap();
    input.trim().to_owned()
}

/// Let the user pass the token in an environment variable, or prompt them if that's not found.
fn get_oauth2_token() -> String {
    env::var("DBX_OAUTH_TOKEN").unwrap_or_else(|_| {
        let client_id = prompt("Give me a Dropbox API app key");
        let client_secret = prompt("Give me a Dropbox API app secret");

        let url = Oauth2AuthorizeUrlBuilder::new(&client_id, Oauth2Type::AuthorizationCode).build();
        eprintln!("Open this URL in your browser:");
        eprintln!("{}", url);
        eprintln!();
        let auth_code = prompt("Then paste the code here");

        eprintln!("requesting OAuth2 token");
        match oauth2_token_from_authorization_code(
            NoauthDefaultClient::default(), &client_id, &client_secret, auth_code.trim(), None)
        {
            Ok(token) => {
                eprintln!("got token: {}", token);

                // This is where you'd save the token somewhere so you don't need to do this dance
                // again.

                token
            },
            Err(e) => {
                eprintln!("Error getting OAuth2 token: {}", e);
                std::process::exit(1);
            }
        }
    })
}

fn main() {
    env_logger::init();

    let download_path = match parse_args() {
        Operation::Usage => {
            eprintln!("usage: {} [option]", std::env::args().next().unwrap());
            eprintln!("    options:");
            eprintln!("        --help | -h      view this text");
            eprintln!("        --list           list all files in your Dropbox");
            eprintln!("        <path>           print the file at the given path to stdout");
            eprintln!();
            eprintln!("    If a Dropbox OAuth token is given in the environment variable");
            eprintln!("    DBX_OAUTH_TOKEN, it will be used, otherwise you will be prompted for");
            eprintln!("    authentication interactively.");
            std::process::exit(1);
        },
        Operation::List => None,
        Operation::Download { path } => Some(path),
    };

    let client = UserAuthDefaultClient::new(get_oauth2_token());

    if let Some(path) = download_path {
        eprintln!("downloading file {}", path);
        eprintln!();
        let mut bytes_out = 0u64;
        let download_arg = files::DownloadArg::new(path);
        let stdout = io::stdout();
        let mut stdout_lock = stdout.lock();
        'download: loop {
            let result = files::download(&client, &download_arg, Some(bytes_out), None);
            match result {
                Ok(Ok(download_result)) => {
                    let mut body = download_result.body.expect("no body received!");
                    loop {
                        // limit read to 1 MiB per loop iteration so we can output progress
                        let mut input_chunk = (&mut body).take(1024 * 1024);
                        match io::copy(&mut input_chunk, &mut stdout_lock) {
                            Ok(0) => {
                                eprint!("\r");
                                break 'download;
                            }
                            Ok(len) => {
                                bytes_out += len as u64;
                                if let Some(total) = download_result.content_length {
                                    eprint!("\r{:.01}%",
                                        bytes_out as f64 / total as f64 * 100.);
                                } else {
                                    eprint!("\r{} bytes", bytes_out);
                                }
                            }
                            Err(e) => {
                                eprintln!("Read error: {}", e);
                                continue 'download; // do another request and resume
                            }
                        }
                    }
                },
                Ok(Err(download_error)) => {
                    eprintln!("Download error: {}", download_error);
                },
                Err(request_error) => {
                    eprintln!("Error: {}", request_error);
                }
            }
            break 'download;
        }
    } else {
        eprintln!("listing all files");
        match list_directory(&client, "/", true) {
            Ok(Ok(iterator)) => {
                for entry_result in iterator {
                    match entry_result {
                        Ok(Ok(files::Metadata::Folder(entry))) => {
                            println!("Folder: {}", entry.path_display.unwrap_or(entry.name));
                        },
                        Ok(Ok(files::Metadata::File(entry))) => {
                            println!("File: {}", entry.path_display.unwrap_or(entry.name));
                        },
                        Ok(Ok(files::Metadata::Deleted(entry))) => {
                            panic!("unexpected deleted entry: {:?}", entry);
                        },
                        Ok(Err(e)) => {
                            eprintln!("Error from files/list_folder_continue: {}", e);
                            break;
                        },
                        Err(e) => {
                            eprintln!("API request error: {}", e);
                            break;
                        },
                    }
                }
            },
            Ok(Err(e)) => {
                eprintln!("Error from files/list_folder: {}", e);
            },
            Err(e) => {
                eprintln!("API request error: {}", e);
            }
        }
    }
}

fn list_directory<'a, T: UserAuthClient>(client: &'a T, path: &str, recursive: bool)
    -> dropbox_sdk::Result<Result<DirectoryIterator<'a, T>, files::ListFolderError>>
{
    assert!(path.starts_with('/'), "path needs to be absolute (start with a '/')");
    let requested_path = if path == "/" {
        // Root folder should be requested as empty string
        String::new()
    } else {
        path.to_owned()
    };
    match files::list_folder(
        client,
        &files::ListFolderArg::new(requested_path)
            .with_recursive(recursive))
    {
        Ok(Ok(result)) => {
            let cursor = if result.has_more {
                Some(result.cursor)
            } else {
                None
            };

            Ok(Ok(DirectoryIterator {
                client,
                cursor,
                buffer: result.entries.into(),
            }))
        },
        Ok(Err(e)) => Ok(Err(e)),
        Err(e) => Err(e),
    }
}

struct DirectoryIterator<'a, T: UserAuthClient> {
    client: &'a T,
    buffer: VecDeque<files::Metadata>,
    cursor: Option<String>,
}

impl<'a, T: UserAuthClient> Iterator for DirectoryIterator<'a, T> {
    type Item = dropbox_sdk::Result<Result<files::Metadata, files::ListFolderContinueError>>;

    fn next(&mut self) -> Option<Self::Item> {
        if let Some(entry) = self.buffer.pop_front() {
            Some(Ok(Ok(entry)))
        } else if let Some(cursor) = self.cursor.take() {
            match files::list_folder_continue(self.client, &files::ListFolderContinueArg::new(cursor)) {
                Ok(Ok(result)) => {
                    self.buffer.extend(result.entries.into_iter());
                    if result.has_more {
                        self.cursor = Some(result.cursor);
                    }
                    self.buffer.pop_front().map(|entry| Ok(Ok(entry)))
                },
                Ok(Err(e)) => Some(Ok(Err(e))),
                Err(e) => Some(Err(e)),
            }
        } else {
            None
        }
    }
}
