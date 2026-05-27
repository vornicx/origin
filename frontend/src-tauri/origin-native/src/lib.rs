#[cxx::bridge(namespace = "origin::native")]
mod ffi {
    unsafe extern "C++" {
        include!("origin-native/src/cpp/hello.h");

        fn hello_cxx() -> String;
    }
}

pub fn hello_native() -> String {
    ffi::hello_cxx()
}
