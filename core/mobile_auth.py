"""Small token adapter for native clients; web session authentication is unchanged."""
from django.contrib.auth import authenticate
from rest_framework import permissions, serializers, status
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import UserSerializer


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(trim_whitespace=False)


class MobileLoginAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(request, **serializer.validated_data)
        if not user or not user.is_active:
            return Response({"detail": "Invalid username or password."}, status=status.HTTP_401_UNAUTHORIZED)
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key, "user": UserSerializer(user, context={"request": request}).data})


class MobileLogoutAPIView(APIView):
    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MobileMeAPIView(APIView):
    def get(self, request):
        return Response(UserSerializer(request.user, context={"request": request}).data)
