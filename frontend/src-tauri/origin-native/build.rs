fn main() {
    cxx_build::bridge("src/lib.rs")
        .file("src/cpp/hello.cc")
        .std("c++17")
        .compile("origin_native");

    println!("cargo:rerun-if-changed=src/lib.rs");
    println!("cargo:rerun-if-changed=src/cpp/hello.cc");
    println!("cargo:rerun-if-changed=src/cpp/hello.h");
}
