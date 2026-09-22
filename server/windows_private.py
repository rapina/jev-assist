"""Windows handles and owner-only DACLs for Jev's local files (pywin32)."""
import msvcrt
import os
import stat

import win32api
import win32con
import win32file
import win32security as security
import pywintypes


def _sid():
    token = security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        return security.GetTokenInformation(token, security.TokenUser)[0]
    finally:
        token.Close()


def _acl():
    acl = security.ACL()
    acl.AddAccessAllowedAce(security.ACL_REVISION, win32file.FILE_ALL_ACCESS, _sid())
    return acl


def private(fd):
    descriptor = security.GetSecurityInfo(msvcrt.get_osfhandle(fd), security.SE_FILE_OBJECT,
                                          security.DACL_SECURITY_INFORMATION | security.OWNER_SECURITY_INFORMATION)
    current_sid = _sid()
    if descriptor.GetSecurityDescriptorOwner() != current_sid:
        return False
    acl = descriptor.GetSecurityDescriptorDacl()
    if acl is None or not descriptor.GetSecurityDescriptorControl()[0] & security.SE_DACL_PROTECTED:
        return False
    own_access = False
    for index in range(acl.GetAceCount()):
        ace = acl.GetAce(index)
        if len(ace) != 3:
            return False
        (kind, flags), mask, sid = ace
        if flags & security.INHERITED_ACE:
            return False
        if kind != security.ACCESS_ALLOWED_ACE_TYPE or sid != current_sid:
            return False
        own_access |= mask & win32file.FILE_ALL_ACCESS == win32file.FILE_ALL_ACCESS
    return own_access


def protect(fd):
    security.SetSecurityInfo(msvcrt.get_osfhandle(fd), security.SE_FILE_OBJECT,
                             security.DACL_SECURITY_INFORMATION | security.PROTECTED_DACL_SECURITY_INFORMATION,
                             None, None, _acl(), None)


def open_file(path, flags):
    writable = bool(flags & (os.O_WRONLY | os.O_RDWR))
    # protect_logs also repairs existing log ACLs through read handles.
    access = win32con.GENERIC_READ | win32con.READ_CONTROL | win32con.WRITE_DAC
    if writable:
        access |= win32con.GENERIC_WRITE
    disposition = win32con.OPEN_EXISTING
    attributes = None
    if flags & os.O_CREAT:
        disposition = win32con.CREATE_NEW if flags & os.O_EXCL else win32con.OPEN_ALWAYS
        attributes = pywintypes.SECURITY_ATTRIBUTES()
        attributes.SECURITY_DESCRIPTOR.SetSecurityDescriptorOwner(_sid(), False)
        attributes.SECURITY_DESCRIPTOR.SetSecurityDescriptorDacl(True, _acl(), False)
        attributes.SECURITY_DESCRIPTOR.SetSecurityDescriptorControl(security.SE_DACL_PROTECTED,
                                                                   security.SE_DACL_PROTECTED)
    try:
        handle = win32file.CreateFile(str(path), access,
                                     win32con.FILE_SHARE_READ | win32con.FILE_SHARE_DELETE,
                                     attributes, disposition, win32file.FILE_FLAG_OPEN_REPARSE_POINT, None)
        try:
            info = win32file.GetFileInformationByHandle(handle)
            if info[0] & (win32con.FILE_ATTRIBUTE_REPARSE_POINT | win32con.FILE_ATTRIBUTE_DIRECTORY):
                raise OSError("Refusing a reparse point or directory")
            fd = msvcrt.open_osfhandle(int(handle), flags | os.O_BINARY)
            handle.Detach()
        except BaseException:
            handle.Close()
            raise
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise OSError("Expected a regular file")
        return fd
    except pywintypes.error as error:
        raise OSError(error.winerror, "Windows private-file operation failed") from None
