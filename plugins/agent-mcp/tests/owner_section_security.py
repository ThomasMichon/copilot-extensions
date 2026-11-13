"""Binary section permissions; SDDL may abbreviate a built-in account SID."""

import ctypes as c


def assert_private_section(native, mapping):
    pointer, integer = c.c_void_p, c.c_uint32
    query = native.a.GetSecurityInfo
    query.argtypes = [pointer, integer, integer] + [c.POINTER(pointer)] * 5
    query.restype = integer
    control_query = native.a.GetSecurityDescriptorControl
    control_query.argtypes = [pointer, c.POINTER(c.c_uint16), c.POINTER(integer)]
    control_query.restype = c.c_int32
    acl_query = native.a.GetAclInformation
    acl_query.argtypes = [pointer, pointer, integer, integer]
    acl_query.restype = c.c_int32
    ace_query = native.a.GetAce
    ace_query.argtypes = [pointer, integer, c.POINTER(pointer)]
    ace_query.restype = c.c_int32
    owner, dacl, descriptor = pointer(), pointer(), pointer()
    assert query(
        mapping, 6, 5, c.byref(owner), None, c.byref(dacl), None, c.byref(descriptor),
    ) == 0
    try:
        control, revision = c.c_uint16(), integer()
        native.require(control_query(descriptor, c.byref(control), c.byref(revision)))
        assert control.value & 0x1000
        assert dacl.value is not None
        information = (integer * 3)()
        native.require(acl_query(dacl, information, c.sizeof(information), 2))
        assert information[0] == 1
        ace = pointer()
        native.require(ace_query(dacl, 0, c.byref(ace)))
        assert c.c_ubyte.from_address(ace.value).value == 0
        assert c.c_ubyte.from_address(ace.value + 1).value == 0
        assert c.c_uint16.from_address(ace.value + 2).value >= 8
        assert integer.from_address(ace.value + 4).value == 0xF001F
        for sid in (owner, pointer(ace.value + 8)):
            text = pointer()
            native.require(native.a.ConvertSidToStringSidW(sid, c.byref(text)))
            try:
                assert c.wstring_at(text) == native.current_sid()
            finally:
                native.free(text)
    finally:
        native.free(descriptor)
