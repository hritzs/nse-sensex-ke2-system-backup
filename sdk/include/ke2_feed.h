#ifndef KE2_FEED_H
#define KE2_FEED_H
#include <stdint.h>
#ifdef _WIN32
# define KE2_CALL __cdecl
# ifdef KE2_FEED_BUILD
#  define KE2_API __declspec(dllexport)
# else
#  define KE2_API __declspec(dllimport)
# endif
#else
# define KE2_CALL
# define KE2_API __attribute__((visibility("default")))
#endif
#ifdef __cplusplus
extern "C" {
#endif
typedef struct ke2_feed_context* ke2_feed_handle;
typedef void (KE2_CALL *ke2_feed_callback)(void* user, const char* utf8_json, uint32_t length);
enum { KE2_OK=0, KE2_INVALID_ARGUMENT=-1, KE2_INVALID_STATE=-2,
       KE2_IO_ERROR=-3, KE2_INTERNAL_ERROR=-4 };
enum { KE2_NSE_FO=1, KE2_BSE_FO=2, KE2_BSE_EQ=3 };
typedef struct ke2_feed_stream {
    uint32_t struct_size;
    uint32_t exchange;
    const char* multicast_group;
    const char* local_interface; /* IPv4 or NULL for 0.0.0.0 */
    uint32_t port;
} ke2_feed_stream;
typedef struct ke2_feed_stats {
    uint32_t struct_size;
    uint32_t running;
    uint64_t datagrams;
    uint64_t events;
    uint64_t socket_errors;
    uint64_t callback_errors;
    uint64_t last_received_ms;
} ke2_feed_stats;
/* ABI v1, x64. One context per process. NULL master path disables that exchange.
   At least one valid nonempty master and a callback are required.
   All strings are UTF-8. All errors are returned; C++ exceptions never escape.
   Callback pointers are valid only during the callback; copy bytes to retain.
   Callbacks must return promptly and must not call context API functions.
   Destroy must be externally synchronized with all other API calls. */
KE2_API uint32_t KE2_CALL ke2_feed_abi_version(void);
KE2_API const char* KE2_CALL ke2_feed_last_error(void); /* calling-thread local */
KE2_API int32_t KE2_CALL ke2_feed_create(const char* nse_master, const char* bse_master,
    ke2_feed_callback callback, void* user, ke2_feed_handle* out);
KE2_API int32_t KE2_CALL ke2_feed_reload_masters(ke2_feed_handle,
    const char* nse_master, const char* bse_master); /* stopped only */
KE2_API int32_t KE2_CALL ke2_feed_instruments(ke2_feed_handle, uint32_t exchange,
    ke2_feed_callback callback, void* user); /* exchange 0 = all */
KE2_API int32_t KE2_CALL ke2_feed_process_datagram(ke2_feed_handle,
    uint32_t exchange, const uint8_t* data, uint32_t length); /* stopped only */
KE2_API int32_t KE2_CALL ke2_feed_start(ke2_feed_handle,
    const ke2_feed_stream* streams, uint32_t count); /* NULL,0 = three feed defaults */
KE2_API int32_t KE2_CALL ke2_feed_stop(ke2_feed_handle); /* joins workers */
KE2_API int32_t KE2_CALL ke2_feed_get_stats(ke2_feed_handle, ke2_feed_stats*);
KE2_API int32_t KE2_CALL ke2_feed_destroy(ke2_feed_handle);
#ifdef __cplusplus
}
#endif
#endif
