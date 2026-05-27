#include "hello.h"

namespace origin {
namespace native {

rust::String hello_cxx() {
    return rust::String("hola desde C++ (origin-native via cxx FFI)");
}

}  // namespace native
}  // namespace origin
